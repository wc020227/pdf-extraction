FROM python:3.9-slim

WORKDIR /app

# 设置国内 pip 源（可选）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p uploads processed

EXPOSE 5000

CMD ["python", "app.py"]