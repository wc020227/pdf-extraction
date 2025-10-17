import os
import base64
import io
import time
import uuid
import requests
import json
from pathlib import Path
from flask import Flask, render_template, request, send_file, jsonify
import fitz  # PyMuPDF
from PIL import Image
import pandas as pd
from collections import defaultdict
import pdfplumber
import re

app = Flask(__name__)

# 使用相对路径，避免权限问题
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['PROCESSED_FOLDER'] = 'processed'
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB限制

# 确保上传和处理目录存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['PROCESSED_FOLDER'], exist_ok=True)

zoom_factor = 2.0

# 目标层号配置
TARGET_LAYERS = ['①1', '①2', '②1', '②2', '②3', '③', '④', '⑤1', '⑥', '⑦11', '⑦12', '⑦21', '⑦22', '⑦23', '⑨']


def image_to_base64(img: Image.Image, ext: str = "png") -> str:
    """PIL.Image → base64 data URI"""
    mime = "jpeg" if ext in {"jpg", "jpeg"} else "png"
    buf = io.BytesIO()
    img.save(buf, format=mime.upper())
    byte_data = buf.getvalue()
    b64 = base64.b64encode(byte_data).decode()
    return f"data:image/{mime};base64,{b64}"


def call_qwen_api(image_base64, extraction_type, custom_prompt):
    """直接使用 requests 调用 Qwen API"""
    # 根据提取类型设置提示
    if extraction_type == "drill_data":
        system_prompt = "你是一个地质勘探专家，需要从图片中提取钻孔数据。"
        user_prompt = "提取图片的钻孔编号、坐标、层次、层深、层厚、层底标高列"
        csv_format = "请将提取的结果以CSV格式呈现，必须包含以下表头：钻孔编号,坐标（x，y),层次,层深,层厚,层底标高，坐标后数值直接拼接，中间用一个半角空格隔开。每行代表一条数据，仅返回CSV内容，不要添加任何额外说明文字"
    elif extraction_type == "soil_data":
        system_prompt = "你是一个地质专家，需要从图片中提取静力触探空数据。"
        user_prompt = "提取图片的孔号、孔深、孔口标高、层序、层深、标高"
        csv_format = "请将提取的结果以CSV格式呈现，必须包含以下表头：孔号、孔深、孔口标高、层序、层深、标高。每行代表一条数据，仅返回CSV内容，不要添加任何额外说明文字"
    else:
        system_prompt = "你是一个可以理解图片内容的助手，需要详细描述图片并回答相关问题。"
        user_prompt = custom_prompt
        csv_format = "请将提取的结果以CSV格式呈现，每行代表一条数据，仅返回CSV内容，不要添加任何额外说明文字"

    # 构建请求
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-1530a345f1da48118f765aba1409dd91"
    }

    payload = {
        "model": "qwen-vl-max-2025-08-13",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": image_base64}}
            ]},
            {"role": "user", "content": csv_format}
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        return f"错误: {str(e)}"


def extract_data_from_image(img: Image.Image, extraction_type: str, custom_prompt: str = ""):
    """从单张图片提取数据，返回数据行列表"""
    for attempt in range(2):
        try:
            image_base64 = image_to_base64(img)
            csv_result = call_qwen_api(image_base64, extraction_type, custom_prompt)

            if csv_result.startswith("错误:"):
                raise Exception(csv_result)

            lines = csv_result.strip().splitlines()
            if not lines:
                return []

            # 跳过表头，只返回数据行
            data_lines = []
            header_processed = False

            for line in lines:
                # 跳过空行和明显的表头行
                if not line.strip() or '钻孔编号' in line or '孔号' in line or '层次' in line:
                    if not header_processed and (',' in line or '，' in line):
                        header_processed = True
                    continue

                # 处理数据行
                if ',' in line or '，' in line:
                    # 统一使用英文逗号
                    line = line.replace('，', ',')
                    data_lines.append(line)

            return data_lines
        except Exception as e:
            print(f"⚠️ 第{attempt + 1}次识别失败：{e}")
            time.sleep(1)

    return []


# ============================ AI图像识别功能 ============================

def process_pdf_with_ai(pdf_path: Path, extraction_type: str, custom_prompt: str = "", csv_writer=None):
    """使用AI处理单个PDF文件"""
    pdf_name = pdf_path.stem
    data_count = 0

    try:
        doc = fitz.open(pdf_path)
        total_pages = doc.page_count

        # 限制处理的页数
        max_pages = min(total_pages, 10)

        for page_idx in range(max_pages):
            page = doc[page_idx]
            mat = fitz.Matrix(zoom_factor, zoom_factor)
            pix = page.get_pixmap(matrix=mat)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            width, height = img.size

            # 左右分割
            left_img = img.crop((0, 0, width // 2, height))
            right_img = img.crop((width // 2, 0, width, height))

            # 处理左右两部分图片
            for side, pil_img in zip(("left", "right"), (left_img, right_img)):
                data_lines = extract_data_from_image(pil_img, extraction_type, custom_prompt)

                if data_lines:
                    # 将数据写入CSV文件
                    for line in data_lines:
                        if csv_writer:
                            csv_writer.write_line(line, pdf_name, page_idx + 1, side)
                            data_count += 1

        doc.close()
        return data_count
    except Exception as e:
        print(f"❌ AI处理 {pdf_path} 出错：{e}")
        return data_count


class AI_CSVWriter:
    """AI图像识别的CSV写入器"""

    def __init__(self, csv_path, extraction_type):
        self.csv_path = csv_path
        self.extraction_type = extraction_type
        self.last_drill_id = None
        self.current_drill_id = None
        self.previous_file_last_drill_id = None
        self.current_file_first_drill_id = None
        self.file_initialized = False
        self.is_first_file = True
        self.is_first_line_of_first_file = True

        # 初始化CSV文件，写入表头
        self._write_header()

    def _write_header(self):
        """写入CSV表头"""
        if self.extraction_type == "drill_data":
            header = "钻孔编号,坐标（x，y),层次,层深,层厚,层底标高\n"
        elif self.extraction_type == "soil_data":
            header = "孔号,孔深,孔口标高,层序,层深,标高\n"
        else:
            header = "提取结果\n"

        with open(self.csv_path, "w", encoding="utf-8") as f:
            f.write(header)
        self.file_initialized = True

    def _extract_drill_id(self, data_line):
        """从数据行中提取钻孔编号或孔号"""
        if not data_line.strip():
            return None

        columns = data_line.split(',')
        if not columns:
            return None

        # 只检查第一列是否有钻孔编号
        if columns[0].strip():
            if any(char.isalnum() for char in columns[0].strip()):
                drill_id = columns[0].strip()
                return drill_id

        return None

    def _get_drill_id_suffix(self, drill_id):
        """获取钻孔编号的后缀（后三个字符）"""
        if not drill_id:
            return None
        return drill_id[-3:] if len(drill_id) >= 3 else drill_id

    def _should_omit_columns(self, current_drill_id):
        """判断是否应该省略列（同一个钻孔的续行）"""
        if not current_drill_id or not self.last_drill_id:
            return False

        current_suffix = self._get_drill_id_suffix(current_drill_id)
        last_suffix = self._get_drill_id_suffix(self.last_drill_id)

        return current_suffix == last_suffix

    def _format_continuation_line(self, data_line):
        """格式化续行（省略前几列）"""
        columns = data_line.split(',')

        if self.extraction_type == "drill_data":
            if len(columns) > 2:
                return ','.join(['', ''] + columns[2:])
        elif self.extraction_type == "soil_data":
            if len(columns) > 3:
                return ','.join(['', '', ''] + columns[3:])

        return data_line

    def write_line(self, data_line, pdf_name, page_num, side):
        """写入一行数据，根据钻孔编号智能分组"""
        if not data_line.strip():
            return

        # 提取当前行的钻孔编号
        current_drill_id = self._extract_drill_id(data_line)

        # 判断是否需要添加间隔
        need_separator = False

        # 处理跨文件钻孔编号比较
        if current_drill_id:
            if self.current_file_first_drill_id is None:
                self.current_file_first_drill_id = current_drill_id

                if not self.is_first_file and self.previous_file_last_drill_id is not None:
                    current_suffix = self._get_drill_id_suffix(current_drill_id)
                    previous_suffix = self._get_drill_id_suffix(self.previous_file_last_drill_id)

                    if current_suffix != previous_suffix:
                        need_separator = True
            else:
                if self.last_drill_id is not None:
                    current_suffix = self._get_drill_id_suffix(current_drill_id)
                    last_suffix = self._get_drill_id_suffix(self.last_drill_id)

                    if current_suffix != last_suffix:
                        need_separator = True

            self.current_drill_id = current_drill_id
            self.last_drill_id = current_drill_id

        # 判断是否是续行
        if self.is_first_line_of_first_file:
            is_continuation = False
            self.is_first_line_of_first_file = False
        else:
            is_continuation = self._should_omit_columns(current_drill_id if current_drill_id else self.current_drill_id)

        # 格式化要写入的数据行
        if is_continuation and not need_separator:
            write_data_line = self._format_continuation_line(data_line)
        else:
            write_data_line = data_line

        # 写入数据
        with open(self.csv_path, "a", encoding="utf-8") as f:
            if need_separator:
                f.write("\n")
            f.write(write_data_line + "\n")

    def start_new_file(self, pdf_name):
        """开始处理新文件，重置当前文件状态"""
        if not self.is_first_file:
            self.previous_file_last_drill_id = self.last_drill_id
        else:
            self.is_first_file = False

        self.current_file_first_drill_id = None

    def finish_current_file(self, pdf_name):
        """完成当前文件处理"""
        pass


def process_ai_pdf_task(pdf_path, session_id, file_index, total_files, extraction_type, custom_prompt, csv_writer):
    """处理单个PDF的AI任务函数"""
    pdf_name = Path(pdf_path).stem

    # 开始处理新文件
    csv_writer.start_new_file(pdf_name)

    # 使用AI处理PDF文件
    data_count = process_pdf_with_ai(Path(pdf_path), extraction_type, custom_prompt, csv_writer)

    # 完成当前文件处理
    csv_writer.finish_current_file(pdf_name)

    # 更新进度
    progress = {
        'file_index': file_index,
        'total_files': total_files,
        'filename': pdf_name,
        'status': 'completed',
        'data_count': data_count,
        'method': 'ai'
    }

    return progress


# ============================ PDF文本提取功能 ============================

def extract_borehole_data_with_pdfplumber(pdf_path: Path, target_layers=None):
    """
    使用pdfplumber从PDF文件中精确提取钻孔数据，只提取指定的层号
    """
    if target_layers is None:
        target_layers = TARGET_LAYERS

    all_boreholes_data = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                print(f"使用pdfplumber处理第 {page_num + 1} 页...")

                # 提取文本内容
                text = page.extract_text()
                if not text:
                    continue

                # 提取孔号和坐标信息
                hole_info = extract_hole_info(text)
                if not hole_info:
                    print(f"第 {page_num + 1} 页未找到孔号信息，跳过此页")
                    continue

                # 提取层信息，只提取目标层号
                layer_data = extract_layer_data(text, target_layers)

                if not layer_data:
                    print(f"第 {page_num + 1} 页未找到目标层信息，跳过此页")
                    continue

                # 合并孔信息和层信息
                for layer in layer_data:
                    combined_data = {**hole_info, **layer}
                    all_boreholes_data.append(combined_data)
    except Exception as e:
        print(f"pdfplumber处理出错: {e}")

    return all_boreholes_data


def extract_hole_info(text):
    """
    精确提取孔号和坐标信息
    """
    # 多种孔号匹配模式
    hole_patterns = [
        r'孔\s*号\s*[:：]?\s*(\w+)',
        r'钻孔编号\s*[:：]?\s*(\w+)',
        r'孔号\s*(\w+)',
        r'钻孔号\s*(\w+)'
    ]

    hole_number = None
    for pattern in hole_patterns:
        hole_match = re.search(pattern, text)
        if hole_match:
            hole_number = hole_match.group(1)
            break

    if not hole_number:
        return None

    # 匹配坐标 - 多种可能的格式
    x_patterns = [
        r'X\s*[=＝]\s*([-\d\.]+)',
        r'X坐标\s*[:：]?\s*([-\d\.]+)',
        r'X\s*[:：]?\s*([-\d\.]+)'
    ]

    y_patterns = [
        r'Y\s*[=＝]\s*([-\d\.]+)',
        r'Y坐标\s*[:：]?\s*([-\d\.]+)',
        r'Y\s*[:：]?\s*([-\d\.]+)'
    ]

    x_coord = None
    y_coord = None

    for pattern in x_patterns:
        x_match = re.search(pattern, text)
        if x_match:
            x_coord = x_match.group(1)
            break

    for pattern in y_patterns:
        y_match = re.search(pattern, text)
        if y_match:
            y_coord = y_match.group(1)
            break

    return {
        "钻孔编号": hole_number,
        "坐标（x，y)": f"{x_coord if x_coord else ''} {y_coord if y_coord else ''}".strip(),
        "X坐标": x_coord,
        "Y坐标": y_coord
    }


def extract_layer_data(text, target_layers):
    """
    从文本中提取地层信息，只提取指定的层号
    """
    layer_data = []

    # 改进的正则表达式，匹配更多可能的格式
    layer_pattern = r'([①②③④⑤⑥⑦⑧⑨⑩\d]+[a-zA-Z\d]*)\s+([-\d\.]+)\s+([-\d\.]+)\s+([-\d\.]+)'
    matches = re.findall(layer_pattern, text)

    for match in matches:
        # 清理数据，确保格式规范
        layer_num = match[0].strip()
        elevation = match[1].strip()
        depth = match[2].strip()
        thickness = match[3].strip()

        # 只提取目标层号的数据
        if layer_num not in target_layers:
            continue

        # 确保数据格式正确
        if not all([layer_num, elevation, depth, thickness]):
            continue

        # 检查是否为有效数值
        try:
            float(elevation)
            float(depth)
            float(thickness)
        except ValueError:
            continue

        layer_info = {
            "层号": layer_num,
            "标高": elevation,
            "深度": depth,
            "厚度": thickness
        }
        layer_data.append(layer_info)

    return layer_data


class Text_CSVWriter:
    """文本提取的CSV写入器"""

    def __init__(self, csv_path):
        self.csv_path = csv_path
        self.file_initialized = False
        self._write_header()

    def _write_header(self):
        """写入CSV表头"""
        header = "钻孔编号,坐标（x，y),层次,标高,深度,厚度\n"
        with open(self.csv_path, "w", encoding="utf-8") as f:
            f.write(header)
        self.file_initialized = True

    def write_data(self, data):
        """写入数据"""
        with open(self.csv_path, "a", encoding="utf-8") as f:
            for item in data:
                row = [
                    item.get("钻孔编号", ""),
                    item.get("坐标（x，y)", ""),
                    item.get("层次", ""),
                    item.get("标高", ""),
                    item.get("深度", ""),
                    item.get("厚度", ""),

                ]
                f.write(",".join(str(x) for x in row) + "\n")


def process_text_pdf_task(pdf_path, session_id, file_index, total_files, csv_writer):
    """处理单个PDF的文本提取任务函数"""
    pdf_name = Path(pdf_path).stem

    # 使用pdfplumber处理PDF文件
    borehole_data = extract_borehole_data_with_pdfplumber(Path(pdf_path))

    # 将数据写入CSV
    if borehole_data:
        csv_writer.write_data(borehole_data)
        data_count = len(borehole_data)
    else:
        data_count = 0

    # 更新进度
    progress = {
        'file_index': file_index,
        'total_files': total_files,
        'filename': pdf_name,
        'status': 'completed',
        'data_count': data_count,
        'method': 'text'
    }

    return progress


# ============================ 路由处理 ============================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload_ai', methods=['POST'])
def upload_ai_file():
    """AI图像识别上传处理"""
    if 'files' not in request.files:
        return jsonify({'error': '没有选择文件'}), 400

    files = request.files.getlist('files')
    if not files or files[0].filename == '':
        return jsonify({'error': '没有选择文件'}), 400

    # 获取提取类型和自定义提示
    extraction_type = request.form.get('extraction_type', 'drill_data')
    custom_prompt = request.form.get('custom_prompt', '')

    # 生成会话ID
    session_id = str(uuid.uuid4())

    # 保存上传的文件
    pdf_paths = []
    for file in files:
        if file and file.filename.lower().endswith('.pdf'):
            filename = file.filename
            upload_path = os.path.join(app.config['UPLOAD_FOLDER'], session_id, filename)
            os.makedirs(os.path.dirname(upload_path), exist_ok=True)
            file.save(upload_path)
            pdf_paths.append(upload_path)

    if not pdf_paths:
        return jsonify({'error': '没有有效的PDF文件'}), 400

    # 创建单个CSV文件
    csv_filename = f"ai_extracted_data_{session_id}.csv"
    csv_path = os.path.join(app.config['PROCESSED_FOLDER'], csv_filename)

    # 创建AI CSV写入器
    csv_writer = AI_CSVWriter(csv_path, extraction_type)

    # 顺序处理每个PDF文件
    total_data_count = 0
    for i, pdf_path in enumerate(pdf_paths):
        progress = process_ai_pdf_task(pdf_path, session_id, i, len(pdf_paths),
                                       extraction_type, custom_prompt, csv_writer)
        total_data_count += progress['data_count']

        print(f"AI处理进度: {i + 1}/{len(pdf_paths)} - {progress['filename']}")

    # 清理上传的临时文件
    import shutil
    upload_session_dir = os.path.join(app.config['UPLOAD_FOLDER'], session_id)
    if os.path.exists(upload_session_dir):
        shutil.rmtree(upload_session_dir)

    return jsonify({
        'success': True,
        'message': f'AI处理完成，共处理 {len(pdf_paths)} 个PDF文件，提取 {total_data_count} 条数据',
        'download_url': f'/download/{csv_filename}',
        'processing_method': 'ai'
    })


@app.route('/upload_text', methods=['POST'])
def upload_text_file():
    """文本提取上传处理"""
    if 'files' not in request.files:
        return jsonify({'error': '没有选择文件'}), 400

    files = request.files.getlist('files')
    if not files or files[0].filename == '':
        return jsonify({'error': '没有选择文件'}), 400

    # 生成会话ID
    session_id = str(uuid.uuid4())

    # 保存上传的文件
    pdf_paths = []
    for file in files:
        if file and file.filename.lower().endswith('.pdf'):
            filename = file.filename
            upload_path = os.path.join(app.config['UPLOAD_FOLDER'], session_id, filename)
            os.makedirs(os.path.dirname(upload_path), exist_ok=True)
            file.save(upload_path)
            pdf_paths.append(upload_path)

    if not pdf_paths:
        return jsonify({'error': '没有有效的PDF文件'}), 400

    # 创建单个CSV文件
    csv_filename = f"text_extracted_data_{session_id}.csv"
    csv_path = os.path.join(app.config['PROCESSED_FOLDER'], csv_filename)

    # 创建文本提取CSV写入器
    csv_writer = Text_CSVWriter(csv_path)

    # 顺序处理每个PDF文件
    total_data_count = 0
    for i, pdf_path in enumerate(pdf_paths):
        progress = process_text_pdf_task(pdf_path, session_id, i, len(pdf_paths), csv_writer)
        total_data_count += progress['data_count']

        print(f"文本提取进度: {i + 1}/{len(pdf_paths)} - {progress['filename']}")

    # 清理上传的临时文件
    import shutil
    upload_session_dir = os.path.join(app.config['UPLOAD_FOLDER'], session_id)
    if os.path.exists(upload_session_dir):
        shutil.rmtree(upload_session_dir)

    return jsonify({
        'success': True,
        'message': f'文本提取完成，共处理 {len(pdf_paths)} 个PDF文件，提取 {total_data_count} 条数据',
        'download_url': f'/download/{csv_filename}',
        'processing_method': 'text'
    })


@app.route('/download/<filename>')
def download_file(filename):
    file_path = os.path.join(app.config['PROCESSED_FOLDER'], filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({'error': '文件不存在'}), 404

# 定期清理旧文件
def cleanup_old_files():
    """清理一天前的处理文件"""
    import datetime
    now = datetime.datetime.now()
    for filename in os.listdir(app.config['PROCESSED_FOLDER']):
        file_path = os.path.join(app.config['PROCESSED_FOLDER'], filename)
        if os.path.isfile(file_path):
            file_time = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
            if (now - file_time).days > 1:
                os.remove(file_path)

if __name__ == '__main__':
    cleanup_old_files()
    app.run(host='0.0.0.0', port=5000, debug=False)