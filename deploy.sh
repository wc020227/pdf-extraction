#!/bin/bash

echo "=========================================="
echo "  PDF 提取应用 - Docker 部署"
echo "=========================================="
echo

# 配置变量
APP_NAME="pdf-extraction-app"
CONTAINER_NAME="pdf-extractor"
PORT="5000"

echo "步骤 1/6: 检查 Docker 环境..."
if ! command -v docker &> /dev/null; then
    echo "❌ 错误: Docker 未安装"
    echo "请先安装 Docker: curl -fsSL https://get.docker.com | sh"
    exit 1
fi

echo "✅ Docker 已安装: $(docker --version)"

echo "步骤 2/6: 检查项目文件..."
if [ ! -f "Dockerfile" ]; then
    echo "❌ 错误: Dockerfile 不存在"
    exit 1
fi

if [ ! -f "requirements.txt" ]; then
    echo "❌ 错误: requirements.txt 不存在"
    exit 1
fi

if [ ! -f "app.py" ]; then
    echo "❌ 错误: app.py 不存在"
    exit 1
fi

echo "✅ 项目文件检查通过"

echo "步骤 3/6: 创建数据目录..."
mkdir -p uploads processed
chmod 755 uploads processed
echo "✅ 数据目录创建完成"

echo "步骤 4/6: 构建 Docker 镜像..."
docker build -t $APP_NAME .

if [ $? -ne 0 ]; then
    echo "❌ 镜像构建失败"
    exit 1
fi
echo "✅ Docker 镜像构建成功"

echo "步骤 5/6: 停止并清理旧容器..."
docker stop $CONTAINER_NAME 2>/dev/null && echo "✅ 旧容器已停止" || echo "ℹ️ 无运行中的旧容器"
docker rm $CONTAINER_NAME 2>/dev/null && echo "✅ 旧容器已删除" || echo "ℹ️ 无旧容器需要删除"

echo "步骤 6/6: 启动新容器..."
docker run -d \
  -p $PORT:5000 \
  -v $(pwd)/uploads:/app/uploads \
  -v $(pwd)/processed:/app/processed \
  --name $CONTAINER_NAME \
  --restart unless-stopped \
  $APP_NAME

if [ $? -eq 0 ]; then
    echo "✅ 容器启动成功"
else
    echo "❌ 容器启动失败"
    exit 1
fi

# 获取服务器 IP
SERVER_IP=$(hostname -I | awk '{print $1}')

echo
echo "=========================================="
echo "            🎉 部署完成!"
echo "=========================================="
echo
echo "📱 访问地址:"
echo "   本地: http://localhost:$PORT"
echo "   远程: http://$SERVER_IP:$PORT"
echo
echo "🛠️ 管理命令:"
echo "   查看状态: docker ps | grep $CONTAINER_NAME"
echo "   查看日志: docker logs -f $CONTAINER_NAME"
echo "   停止应用: docker stop $CONTAINER_NAME"
echo "   启动应用: docker start $CONTAINER_NAME"
echo "   重启应用: docker restart $CONTAINER_NAME"
echo
echo "📊 验证部署:"
echo "   运行: ./status.sh"
echo