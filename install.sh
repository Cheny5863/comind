#!/usr/bin/env bash
# CoMind 一键部署脚本（首次安装）
# 用法:
#   bash install.sh                       # 同目录有 latest.json / 发布包时
#   bash install.sh <包路径或URL>          # 显式指定发布包
# 语言: LANG=zh（默认）中文输出；其他值英文输出
set -euo pipefail

APP_DIR="$HOME/.comind/app"
BACKUP_DIR="$HOME/.comind/backup"

# 双语输出：LANG=zh 取第一段（中文），否则第二段（英文）
L() { if [ "${LANG:-zh}" = "zh" ]; then echo "$1"; else echo "$2"; fi; }
say() { echo -e "\033[1;32m[install]\033[0m $*"; }
warn() { echo -e "\033[1;33m[install] $(L 警告: Warning:)\033[0m $*"; }
die() { echo -e "\033[1;31m[install] $(L 错误: Error:)\033[0m $*" >&2; exit 1; }

# ── 1. 定位发布包 ──
PKG="${1:-}"
if [ -z "$PKG" ] && [ -f ./latest.json ]; then
    PKG="$(grep -o '"url":"[^"]*"' ./latest.json | head -1 | cut -d'"' -f4)"
fi
if [ -z "$PKG" ] && ls ./comind--linux-x64-*.tar.gz >/dev/null 2>&1; then
    PKG="$(ls ./comind--linux-x64-*.tar.gz | head -1)"
fi
[ -n "$PKG" ] || die "$(L "找不到发布包：请传包路径/URL，或把 tar.gz + latest.json 放在本目录" "Package not found: pass a path/URL, or put tar.gz + latest.json in this directory")"

if [[ "$PKG" =~ ^https?:// ]]; then
    say "$(L "下载发布包:" "Downloading package:") $PKG"
    curl -fsSL -o /tmp/smm-pkg.tar.gz "$PKG"
    PKG=/tmp/smm-pkg.tar.gz
elif [ ! -f "$PKG" ]; then
    die "$(L "文件不存在:" "File not found:") $PKG"
fi

# sha256 校验（有 latest.json 时）
if [ -f ./latest.json ]; then
    EXPECT="$(grep -o '"sha256":"[^"]*"' ./latest.json | head -1 | cut -d'"' -f4)"
    ACTUAL="$(sha256sum "$PKG" | awk '{print $1}')"
    [ "$EXPECT" = "$ACTUAL" ] || die "$(L "sha256 校验失败（期望 $EXPECT，实际 $ACTUAL）" "sha256 mismatch (expected $EXPECT, got $ACTUAL)")"
    say "$(L "sha256 校验通过" "sha256 verified")"
fi

# ── 2. 解压到 ~/.comind/app（旧版备份） ──
mkdir -p "$APP_DIR" "$BACKUP_DIR"
if [ -f "$APP_DIR/VERSION" ]; then
    mv "$APP_DIR" "$BACKUP_DIR/app.$(date +%s)"
    say "$(L "检测到旧版本，已备份" "Old version found, backed up")"
fi
mkdir -p "$APP_DIR"
tar xzf "$PKG" -C "$APP_DIR" --strip-components=1
chmod +x "$APP_DIR/comind-server/comind-server" 2>/dev/null || true
VER="$(cat "$APP_DIR/VERSION" 2>/dev/null || echo '?')"
say "$(L "已安装版本 $VER → $APP_DIR" "Installed version $VER → $APP_DIR")"

# 脑图数据目录（程序外，升级不动）
mkdir -p "$HOME/comind-maps"

# 分发源的 latest.json 放到 ~/.comind/（供 update.sh 对比版本）
if [ -f ./latest.json ]; then
    cp ./latest.json "$HOME/.comind/latest.json"
    say "$(L "已保存更新源清单 → $HOME/.comind/latest.json" "Update source saved → $HOME/.comind/latest.json")"
fi

# ── 3. AI 助理运行时（pi，必要功能）：缺失则自动安装 ──
# node 从 nodejs.org 公开源下载，pi 从 npm registry 安装（免编译，含 linux prebuild）
NODE_DIR="$HOME/.node"
NPM_GLOBAL="$HOME/.npm-global"
PI_PATH="$NPM_GLOBAL/bin/pi"
NODE_VER="v24.15.0"

if [ -x "$PI_PATH" ]; then
    say "$(L "AI 助理运行时已就绪:" "AI assistant runtime ready:") $PI_PATH"
elif [ -x "$APP_DIR/smm-pi" ]; then
    say "$(L "使用发布包内置 smm-pi 二进制（免 node）" "Using bundled smm-pi binary (no node needed)")"
else
    # 定位 node：系统 → ~/.node → 在线下载
    NODE_BIN=""
    if command -v node >/dev/null 2>&1; then
        NODE_BIN="$(dirname "$(command -v node)")"
        say "$(L "使用系统 node" "Using system node") $(node --version)"
    elif [ -x "$NODE_DIR/bin/node" ]; then
        NODE_BIN="$NODE_DIR/bin"
        say "$(L "使用 ~/.node 已安装的 node" "Using node from ~/.node")"
    else
        say "$(L "下载 node $NODE_VER（nodejs.org 公开源）..." "Downloading node $NODE_VER (from nodejs.org)...")"
        curl -fsSL -o /tmp/node.tar.xz "https://nodejs.org/dist/$NODE_VER/node-$NODE_VER-linux-x64.tar.xz" || die "$(L "node 下载失败：目标机需能访问 nodejs.org" "node download failed: host needs access to nodejs.org")"
        mkdir -p "$NODE_DIR"
        tar xJf /tmp/node.tar.xz -C "$NODE_DIR" --strip-components=1
        rm -f /tmp/node.tar.xz
        NODE_BIN="$NODE_DIR/bin"
        say "$(L "node $NODE_VER 已装到 $NODE_DIR" "node $NODE_VER installed to $NODE_DIR")"
    fi
    # 安装 pi（npm registry 公开源）
    say "$(L "npm 安装 @earendil-works/pi-coding-agent（npm registry）..." "Installing @earendil-works/pi-coding-agent via npm registry...")"
    export PATH="$NODE_BIN:$PATH"
    mkdir -p "$NPM_GLOBAL"
    "$NODE_BIN/npm" install -g --prefix "$NPM_GLOBAL" @earendil-works/pi-coding-agent || die "$(L "pi 安装失败：npm registry 需可达" "pi install failed: npm registry must be reachable")"
fi
[ -x "$PI_PATH" ] || die "$(L "AI 助理运行时（pi）未就绪——这是必要功能，请检查网络后重跑本脚本" "AI assistant runtime (pi) not ready — required feature, check network and rerun")"
say "$(L "AI 助理运行时就绪: pi" "AI assistant runtime ready: pi") $("$PI_PATH" --version 2>/dev/null || echo '?')（node: $("$NODE_DIR/bin/node" --version 2>/dev/null || node --version 2>/dev/null || echo '?')）"

# ── 4. 安装 systemd 守护 ──
SERVICE_TEMPLATE="$APP_DIR/comind.service"
if [ "$(id -u)" = "0" ]; then
    REAL_USER="${SUDO_USER:-root}"
    REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
    sed -e "s|%h|$REAL_HOME|g" -e "s|%USER%|$REAL_USER|g" "$SERVICE_TEMPLATE" > /etc/systemd/system/comind.service
    systemctl daemon-reload
    systemctl enable --now comind.service
    say "$(L "已注册系统级 systemd 服务并启动" "System-level systemd service registered and started")"
else
    mkdir -p "$HOME/.config/systemd/user"
    sed -e "s|%h|$HOME|g" -e "/^User=/d" "$SERVICE_TEMPLATE" > "$HOME/.config/systemd/user/comind.service"
    systemctl --user daemon-reload
    systemctl --user enable --now comind.service
    loginctl enable-linger "$USER" 2>/dev/null || true
    say "$(L "已注册用户级 systemd 服务并启动（enable-linger 保持后台运行）" "User-level systemd service registered and started (enable-linger keeps it running)")"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "$(L "部署完成 ✓  访问地址:" "Deployment complete ✓  Visit:") http://${IP:-localhost}:8789"
say "$(L "查看状态: systemctl status comind （用户级: systemctl --user status comind）" "Status: systemctl status comind (user-level: systemctl --user status comind)")"
