#!/bin/bash
# 分发 comind 包到 movexbot（n2n 分块传输 + sha256 校验 + 包内 update.sh 安装）
set -euo pipefail
VER=$(cat /home/cheny/projects/comind/VERSION)
SRC=/home/cheny/projects/comind/dist-release
PKG=comind-${VER}-linux-x86_64.tar.gz
SHA=$(sha256sum "$SRC/$PKG" | cut -d' ' -f1)
HOST=movexbot@10.88.88.88
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15 $HOST"
SCP="scp -o BatchMode=yes -o ConnectTimeout=15"

echo "==> 版本 $VER sha256=$SHA"
$SSH "mkdir -p ~/.comind/upload && rm -f ~/.comind/upload/$PKG.* ~/.comind/upload/$PKG"

WORK=/tmp/comind-split-$VER
rm -rf "$WORK"; mkdir -p "$WORK"
split -b 4M -d -a 2 "$SRC/$PKG" "$WORK/${PKG}.part."
N=$(ls "$WORK" | wc -l)
echo "==> 分块 $N 块"

for i in $(seq 0 $((N-1))); do
  part=$(printf "%s.part.%02d" "$PKG" "$i")
  for try in 1 2 3; do
    if $SCP "$WORK/$part" "$HOST:~/.comind/upload/$part" >/dev/null 2>&1; then
      remote=$($SSH "stat -c%s ~/.comind/upload/$part 2>/dev/null || echo 0")
      local_sz=$(stat -c%s "$WORK/$part")
      if [ "$remote" = "$local_sz" ]; then break; fi
    fi
    echo "   重试 $part ($try)"; sleep 2
  done
done

echo "==> 远端合并 + 校验"
$SSH "cd ~/.comind/upload && cat ${PKG}.part.* > $PKG && sha256sum $PKG" | tee /tmp/remote_sha.txt
grep -q "$SHA" /tmp/remote_sha.txt || { echo "FAIL: sha256 不一致"; exit 1; }

echo "==> 覆盖 latest.json + 包到 ~/.comind/"
$SCP "$SRC/latest.json" "$HOST:~/.comind/latest.json" >/dev/null
$SSH "mv ~/.comind/upload/$PKG ~/.comind/$PKG && ls -la ~/.comind/$PKG ~/.comind/latest.json"

echo "==> 包内 update.sh 安装"
$SSH "rm -rf /tmp/comind-upd && mkdir -p /tmp/comind-upd && tar xzf ~/.comind/$PKG -C /tmp/comind-upd && d=\$(ls -d /tmp/comind-upd/*/ | head -1) && cd \"\$d\" && bash update.sh 2>&1 | tail -25"
echo "DONE"
