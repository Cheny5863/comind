#!/usr/bin/env bash
# CoMind 一键更新脚本（在 ~/.comind/app 或任意目录运行）
# 流程: 拉 latest.json → 对比版本 → 下载 → 校验 sha256 → 备份旧版(保留1份) → 覆盖 → 重启
# 更新源（三选一）:
#   1. 环境变量 SMM_UPDATE_URL=http://内网IP:8000/smm   （内网文件服务器/HTTP 目录）
#   2. 包内 latest.json（本地目录分发：把发布包+latest.json 放到 ~/.comind/app 或同目录）
#   3. latest.json 里写绝对 URL（GitHub Releases 等）
# 语言: LANG=zh（默认）中文输出；其他值英文输出
set -euo pipefail

APP_DIR="${SMM_APP_DIR:-$HOME/.comind/app}"
BACKUP_DIR="$HOME/.comind/backup"

# 双语输出：LANG=zh 取第一段（中文），否则第二段（英文）
L() { if [ "${LANG:-zh}" = "zh" ]; then echo "$1"; else echo "$2"; fi; }
say() { echo -e "\033[1;32m[update]\033[0m $*"; }
warn() { echo -e "\033[1;33m[update] $(L 警告: Warning:)\033[0m $*"; }
die() { echo -e "\033[1;31m[update] $(L 错误: Error:)\033[0m $*" >&2; exit 1; }

[ -f "$APP_DIR/VERSION" ] || die "$(L "未找到 $APP_DIR/VERSION，请先运行 install.sh" "VERSION not found in $APP_DIR, run install.sh first")"
CUR="$(cat "$APP_DIR/VERSION" | tr -d ' \n')"

# ── 1. 获取 latest.json ──
BASE=""
if [ -n "${SMM_UPDATE_URL:-}" ]; then
    say "$(L "更新源:" "Update source:") $SMM_UPDATE_URL"
    curl -fsSL -o /tmp/smm-latest.json "$SMM_UPDATE_URL/latest.json" || die "$(L "拉取 latest.json 失败" "Failed to fetch latest.json")"
    BASE="$SMM_UPDATE_URL"
elif [ -f "$HOME/.comind/latest.json" ]; then
    cp "$HOME/.comind/latest.json" /tmp/smm-latest.json
    BASE="$HOME/.comind"   # 本地目录分发：latest.json 的 url 相对 ~/.comind/ 解析
else
    die "$(L "未配置更新源：设置 SMM_UPDATE_URL 环境变量，或把 latest.json 放到 $HOME/.comind/" "No update source: set SMM_UPDATE_URL, or place latest.json in $HOME/.comind/")"
fi

NEW="$(grep -o '"version":"[^"]*"' /tmp/smm-latest.json | head -1 | cut -d'"' -f4)"
URL="$(grep -o '"url":"[^"]*"' /tmp/smm-latest.json | head -1 | cut -d'"' -f4)"
SHA="$(grep -o '"sha256":"[^"]*"' /tmp/smm-latest.json | head -1 | cut -d'"' -f4)"
[ -n "$NEW" ] || die "$(L "latest.json 格式异常（缺 version）" "latest.json malformed (missing version)")"

if [ "$NEW" = "$CUR" ]; then
    say "$(L "已是最新版本 ($CUR)，无需更新" "Already up to date ($CUR)")"
    exit 0
fi

# ── 2. 下载 ──
case "$URL" in
    http://*|https://*) PKG_URL="$URL" ;;
    /*) PKG_URL="$URL" ;;          # 已是绝对路径（本地文件分发）
    *) PKG_URL="$BASE/$URL" ;;
    esac
say "$(L "发现新版本 $CUR → $NEW" "Found new version $CUR → $NEW")"
say "$(L "下载:" "Downloading:") $PKG_URL"
if [[ "$PKG_URL" == http://* || "$PKG_URL" == https://* ]]; then
    curl -fsSL -o /tmp/smm-update.tar.gz "$PKG_URL" || die "$(L "下载失败" "Download failed")"
else
    # 本地目录分发：PKG_URL 为文件路径
    cp "$PKG_URL" /tmp/smm-update.tar.gz || die "$(L "本地发布包不存在:" "Local package not found:") $PKG_URL"
fi

# ── 3. 校验 sha256 ──
ACT="$(sha256sum /tmp/smm-update.tar.gz | awk '{print $1}')"
[ "$ACT" = "$SHA" ] || die "$(L "sha256 校验失败（期望 $SHA，实际 $ACT），已中止" "sha256 mismatch (expected $SHA, got $ACT), aborted")"
say "$(L "sha256 校验通过" "sha256 verified")"

# ── 4. 备份旧版（保留 1 份可回滚） + 覆盖 ──
mkdir -p "$BACKUP_DIR"
rm -rf "$BACKUP_DIR/app.prev"
[ -d "$APP_DIR" ] && mv "$APP_DIR" "$BACKUP_DIR/app.prev"
mkdir -p "$APP_DIR"
tar xzf /tmp/smm-update.tar.gz -C "$APP_DIR" --strip-components=1
chmod +x "$APP_DIR/comind-server/comind-server" 2>/dev/null || true

# ── 5. 重新生成 systemd 服务文件（模板可能随版本更新，如 ExecStart/Description 变更） ──
if [ -f /etc/systemd/system/comind.service ]; then
    REAL_USER="${SUDO_USER:-root}"
    REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
    sed -e "s|%h|$REAL_HOME|g" -e "s|%USER%|$REAL_USER|g" "$APP_DIR/comind.service" > /etc/systemd/system/comind.service
    systemctl daemon-reload
elif [ -f "$HOME/.config/systemd/user/comind.service" ]; then
    sed -e "s|%h|$HOME|g" -e "/^User=/d" "$APP_DIR/comind.service" > "$HOME/.config/systemd/user/comind.service"
    systemctl --user daemon-reload
else
    warn "$(L "未找到 systemd 服务，请手动安装服务文件后重启" "systemd service not found, install service file and restart manually")"
fi

# ── 6. 重启 systemd 服务 ──
if [ -f /etc/systemd/system/comind.service ]; then
    systemctl restart comind
elif [ -f "$HOME/.config/systemd/user/comind.service" ]; then
    systemctl --user restart comind
else
    warn "$(L "未找到 systemd 服务，请手动重启" "systemd service not found, restart manually")"
fi
say "$(L "已更新到 $NEW 并重启 ✓" "Updated to $NEW and restarted ✓")"
say "$(L "回滚: systemctl stop comind && rm -rf $APP_DIR && mv $BACKUP_DIR/app.prev $APP_DIR && systemctl start comind" "Rollback: systemctl stop comind && rm -rf $APP_DIR && mv $BACKUP_DIR/app.prev $APP_DIR && systemctl start comind")"
