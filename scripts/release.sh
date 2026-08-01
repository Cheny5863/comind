#!/usr/bin/env bash
# simple-mind-map 发布打包脚本（开发机运行）
# 用法: scripts/release.sh [BASE_URL]
#   BASE_URL 可选：内网/HTTP 分发地址（如 http://download.example.com/comind），
#   不传则 latest.json 里 url 用相对路径（配合本地目录分发）。
# 产出: dist-release/
#   comind--linux-x64-<VER>.tar.gz   发布包（含 comind-server 二进制 + 前端资源 + install/update/service）
#   latest.json                   版本清单（version/sha256/url）
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VER="$(cat VERSION | tr -d ' \n')"
BASE_URL="${1:-}"

echo "==> [1/4] 构建前端 (web/)"
(cd web && npm run build)

echo "==> [2/4] PyInstaller 打包后端 (onedir)"
rm -rf build dist/comind-server
pyinstaller --noconfirm --clean \
    --name comind-server --onedir \
    --exclude-module numpy --exclude-module matplotlib --exclude-module PyQt6 --exclude-module PyQt5 \
    --exclude-module pygame --exclude-module jedi --exclude-module IPython --exclude-module zstandard \
    --exclude-module pandas --exclude-module scipy --exclude-module PIL --exclude-module skimage \
    --exclude-module cairo --exclude-module gi --exclude-module tkinter --exclude-module pytest \
    --add-data "dist:dist" \
    --add-data "ai-assistant:ai-assistant" \
    --add-data "pi-ext:pi-ext" \
    --add-data "index.html:." \
    --add-data "map-switcher.js:." \
    --add-data "VERSION:." \
    backend.py

echo "==> [3/4] 组发布目录 + 打 tar.gz"
REL="dist-release"
STAGE="$REL/comind--linux-x64-$VER"
rm -rf "$STAGE" "$REL/comind--linux-x64-$VER.tar.gz" "$REL/latest.json"
mkdir -p "$STAGE"
cp -r dist/comind-server "$STAGE/comind-server"
cp comind.service "$STAGE/comind.service"
cp install.sh "$STAGE/install.sh"
cp update.sh "$STAGE/update.sh"
cp VERSION "$STAGE/VERSION"   # 顶层版本文件，供 install/update 对比
chmod +x "$STAGE/install.sh" "$STAGE/update.sh"
(cd "$REL" && tar czf "comind--linux-x64-$VER.tar.gz" "comind--linux-x64-$VER")
SHA="$(sha256sum "$REL/comind--linux-x64-$VER.tar.gz" | awk '{print $1}')"

echo "==> [4/4] 生成 latest.json"
if [ -n "$BASE_URL" ]; then
    URL="$BASE_URL/comind--linux-x64-$VER.tar.gz"
else
    URL="comind--linux-x64-$VER.tar.gz"
fi
cat > "$REL/latest.json" <<EOF
{"version":"$VER","url":"$URL","sha256":"$SHA"}
EOF

SIZE="$(du -h "$REL/comind--linux-x64-$VER.tar.gz" | cut -f1)"
echo ""
echo "完成 ✓  版本 $VER"
echo "  发布包:  $ROOT/$REL/comind--linux-x64-$VER.tar.gz ($SIZE)"
echo "  latest:  $ROOT/$REL/latest.json"
echo ""
echo "分发方式:"
echo "  1) GitHub Releases: 上传 tar.gz，latest.json 的 url 改为下载直链"
echo "  2) 内网服务器:      python3 -m http.server 8000 -d dist-release，BASE_URL=http://IP:8000"
echo "  3) 本地目录:        scp dist-release/* 到目标机 ~/.comind/app/"
