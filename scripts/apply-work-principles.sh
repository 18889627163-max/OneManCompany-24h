#!/bin/bash
# 原子应用 00002-00012 员工工作原则。
# 默认只修改 work_principles.md 和 revision manifest，不触碰 task_history/progress/task_index。
set -u

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DOCS_DIR="$ROOT_DIR/docs/employee-work-principles"
EMPLOYEES_DIR="$ROOT_DIR/.onemancompany/company/human_resource/employees"
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) echo "Usage: $0 [--dry-run]"; exit 0 ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done

ids=(00002 00003 00004 00005 00006 00007 00008 00009 00010 00011 00012)
revision="24h-v2-$(date -u +%Y%m%dT%H%M%SZ)"
tmp_dir="$EMPLOYEES_DIR/.work-principles-.$$.tmp"
manifest_tmp="$EMPLOYEES_DIR/.work-principles-revision.$$.tmp"
backup_dir="$ROOT_DIR/backups/implementation-snapshots/work-principles-$revision"

fail() { echo "❌ $*" >&2; exit 1; }
cleanup() { rm -rf "$tmp_dir" "$manifest_tmp"; }
trap cleanup EXIT

[ -d "$DOCS_DIR" ] || fail "工作原则文档目录不存在: $DOCS_DIR"
[ -d "$EMPLOYEES_DIR" ] || fail "正式员工目录不存在: $EMPLOYEES_DIR"
mkdir -p "$tmp_dir"

# 全量预检：任何一项不完整都不写入正式目录。
for id in "${ids[@]}"; do
  target="$EMPLOYEES_DIR/$id"
  doc=$(find "$DOCS_DIR" -maxdepth 1 -type f -name "$id-*-work-principles.md" -print -quit)
  [ -d "$target" ] || fail "$id 正式员工目录不存在"
  [ -n "$doc" ] && [ -s "$doc" ] || fail "$id 工作原则文档不存在或为空"
  cp "$doc" "$tmp_dir/$id-work_principles.md" || fail "$id 临时文件创建失败"
  printf '%s\n' "$doc" > "$tmp_dir/$id-source.txt"
done

# 先校验所有临时文件，再构造不可变 hash manifest。
MANIFEST_TMP="$manifest_tmp" TMP_DIR="$tmp_dir" REVISION="$revision" ROOT_DIR="$ROOT_DIR" \
  "$ROOT_DIR/.venv/bin/python" - <<'PY'
from pathlib import Path
import hashlib, json, os
root=Path(os.environ['ROOT_DIR']); tmp=Path(os.environ['TMP_DIR']); out=Path(os.environ['MANIFEST_TMP'])
rows=[]
for p in sorted(tmp.glob('*-work_principles.md')):
    data=p.read_bytes()
    if not data.strip(): raise SystemExit(f'empty principle: {p.name}')
    rows.append({'employee_id':p.name[:5], 'source':Path((tmp/(p.name[:5]+'-source.txt')).read_text().strip()).relative_to(root).as_posix(), 'sha256':hashlib.sha256(data).hexdigest(), 'bytes':len(data)})
out.write_text(json.dumps({'revision':os.environ['REVISION'],'files':rows}, ensure_ascii=False, indent=2)+'\n')
PY

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY RUN: 将原子应用 ${#ids[@]} 份工作原则，revision=$revision"
  cat "$manifest_tmp"
  exit 0
fi

# 保留应用前状态，失败时逐项回滚。历史文件不在写集内。
mkdir -p "$backup_dir"
for id in "${ids[@]}"; do
  if [ -f "$EMPLOYEES_DIR/$id/work_principles.md" ]; then
    cp "$EMPLOYEES_DIR/$id/work_principles.md" "$backup_dir/$id-work_principles.md"
  fi
done
cp "$manifest_tmp" "$backup_dir/MANIFEST.json"

applied=()
rollback() {
  echo "❌ 应用失败，开始回滚工作原则文件" >&2
  for id in "${applied[@]}"; do
    if [ -f "$backup_dir/$id-work_principles.md" ]; then
      cp "$backup_dir/$id-work_principles.md" "$EMPLOYEES_DIR/$id/work_principles.md"
    else
      rm -f "$EMPLOYEES_DIR/$id/work_principles.md"
    fi
  done
  rm -f "$EMPLOYEES_DIR/.work-principles-revision.yaml"
  exit 1
}
trap rollback ERR
for id in "${ids[@]}"; do
  # 同一文件系统内 rename，避免读到半份文件。
  cp "$tmp_dir/$id-work_principles.md" "$EMPLOYEES_DIR/$id/.work_principles.md.tmp"
  mv -f "$EMPLOYEES_DIR/$id/.work_principles.md.tmp" "$EMPLOYEES_DIR/$id/work_principles.md"
  applied+=("$id")
done
cp "$manifest_tmp" "$EMPLOYEES_DIR/.work-principles-revision.yaml"
trap cleanup EXIT

echo "✅ 原子应用完成: ${#ids[@]} 份工作原则 (revision=$revision)"
