#!/usr/bin/env bash

set -Eeuo pipefail

# ========================== INPUT CONFIGURATION ===========================
# Điền đủ 5 MSSV trước khi chạy. Không truyền tham số trên command line.
STUDENT_IDS=(
  "23120029" # MSSV 1
  "23120048" # MSSV 2
  "23120282" # MSSV 3
  "23120302" # MSSV 4
  "23120308" # MSSV 5
)

REPORT_FILE="REPORT.pdf"
OUTPUT_DIRECTORY="submission"
PYTHON_BIN="" # Để trống để tự tìm python/python3 trong môi trường hiện tại.
RUN_VERIFICATION=true
CREATE_ZIP=true
FORCE_REPLACE=true
SIMULATION_SEED=42
# ======================== END INPUT CONFIGURATION =========================

fail() {
  echo "Error: $*" >&2
  exit 1
}

[[ $# -eq 0 ]] || fail "không truyền tham số; hãy sửa INPUT CONFIGURATION trong file"

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
student_ids=("${STUDENT_IDS[@]}")
((${#student_ids[@]} == 5)) || fail "STUDENT_IDS phải có đúng 5 MSSV"

for flag_name in RUN_VERIFICATION CREATE_ZIP FORCE_REPLACE; do
  flag_value=${!flag_name}
  [[ $flag_value == true || $flag_value == false ]] || \
    fail "$flag_name chỉ nhận true hoặc false"
done
[[ $SIMULATION_SEED =~ ^-?[0-9]+$ ]] || fail "SIMULATION_SEED phải là số nguyên"

if [[ $REPORT_FILE = /* ]]; then
  report_path=$REPORT_FILE
else
  report_path="$script_dir/$REPORT_FILE"
fi

if [[ $OUTPUT_DIRECTORY = /* ]]; then
  output_dir=$OUTPUT_DIRECTORY
else
  output_dir="$script_dir/$OUTPUT_DIRECTORY"
fi

python_bin=$PYTHON_BIN
if [[ -z $python_bin ]]; then
  if [[ -x "$script_dir/.venv/bin/python" ]]; then
    python_bin="$script_dir/.venv/bin/python"
  elif command -v python >/dev/null 2>&1; then
    python_bin="python"
  else
    python_bin="python3"
  fi
fi

if $RUN_VERIFICATION; then
  skip_verify=false
else
  skip_verify=true
fi
create_zip=$CREATE_ZIP
force=$FORCE_REPLACE

for student_id in "${student_ids[@]}"; do
  [[ -n $student_id ]] || fail "hãy điền đủ 5 MSSV trong STUDENT_IDS"
  [[ $student_id =~ ^[A-Za-z0-9-]+$ ]] || \
    fail "student IDs may contain only letters, numbers, and hyphens: $student_id"
done

for ((i = 0; i < ${#student_ids[@]}; i++)); do
  for ((j = i + 1; j < ${#student_ids[@]}; j++)); do
    [[ ${student_ids[$i]} != "${student_ids[$j]}" ]] || \
      fail "student IDs must be unique"
  done
done

submission_name="Lab01_$(IFS=_; echo "${student_ids[*]}")"

command -v "$python_bin" >/dev/null 2>&1 || fail "Python interpreter not found: $python_bin"
python_command=$(command -v "$python_bin")
python_bin="$(cd -- "$(dirname -- "$python_command")" && pwd)/$(basename -- "$python_command")"

[[ -f $report_path ]] || fail "REPORT.pdf not found: $report_path"
report_path=$(realpath -- "$report_path")
[[ $(head -c 5 -- "$report_path") == "%PDF-" ]] || fail "report is not a PDF: $report_path"

if command -v pdfinfo >/dev/null 2>&1; then
  page_count=$(pdfinfo "$report_path" | awk -F: '/^Pages:/ {gsub(/[[:space:]]/, "", $2); print $2}')
  [[ -n $page_count ]] || fail "could not determine REPORT.pdf page count"
  ((page_count <= 10)) || fail "REPORT.pdf has $page_count pages; the maximum is 10"
else
  echo "Warning: pdfinfo is unavailable; verify the 10-page limit manually." >&2
fi

required_dirs=(src tests logs config)
for directory in "${required_dirs[@]}"; do
  [[ -d $script_dir/$directory ]] || fail "missing required directory: $directory/"
done
[[ -f $script_dir/README.md ]] || fail "missing required file: README.md"

mkdir -p -- "$output_dir"
output_dir=$(realpath -- "$output_dir")
legal_target="$output_dir/legal/$submission_name"
testing_target="$output_dir/testing/$submission_name"
zip_target="$output_dir/$submission_name.zip"

case "$report_path" in
  "$legal_target"/*|"$testing_target"/*)
    fail "the report source must be outside generated legal/testing targets"
    ;;
esac

remove_target() {
  local target=$1
  [[ -e $target ]] || return 0
  $force || fail "target đã tồn tại; đặt FORCE_REPLACE=true để thay thế: $target"
  rm -rf -- "$target"
}

remove_target "$legal_target"
remove_target "$testing_target"

mkdir -p -- "$legal_target"
for directory in "${required_dirs[@]}"; do
  cp -a -- "$script_dir/$directory" "$legal_target/"
done
cp -a -- "$script_dir/README.md" "$legal_target/README.md"
cp -a -- "$report_path" "$legal_target/REPORT.pdf"

find "$legal_target" -type d \
  \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache -o -name .mypy_cache \) \
  -prune -exec rm -rf -- {} +
find "$legal_target" -type f \
  \( -name '*.pyc' -o -name '*.pyo' -o -name .DS_Store -o -name Thumbs.db -o -name .gitkeep \) \
  -delete

expected_entries=(REPORT.pdf README.md config logs src tests)
for entry in "${expected_entries[@]}"; do
  [[ -e $legal_target/$entry ]] || fail "legal copy is missing: $entry"
done
entry_count=$(find "$legal_target" -mindepth 1 -maxdepth 1 | wc -l)
((entry_count == ${#expected_entries[@]})) || fail "legal copy contains unexpected root entries"

mkdir -p -- "$(dirname -- "$testing_target")"
cp -a -- "$legal_target" "$testing_target"

temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/lab01-submission.XXXXXX")
trap 'rm -rf -- "$temp_dir"' EXIT

hash_file() {
  local file=$1
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -- "$file" | awk '{print $1}'
  else
    shasum -a 256 -- "$file" | awk '{print $1}'
  fi
}

write_manifest() {
  local root=$1
  local destination=$2
  : >"$destination"
  while IFS= read -r -d '' file; do
    relative=${file#"$root"/}
    printf '%s  %s\n' "$(hash_file "$file")" "$relative" >>"$destination"
  done < <(find "$root" -type f -print0 | sort -z)
}

write_manifest "$legal_target" "$temp_dir/legal-before.sha256"
write_manifest "$testing_target" "$temp_dir/testing-before.sha256"
cmp -s "$temp_dir/legal-before.sha256" "$temp_dir/testing-before.sha256" || \
  fail "initial legal and testing copies are not byte-identical"
echo "Initial legal/testing manifests match."

if ! $skip_verify; then
  "$python_bin" -c 'import cryptography, pytest' >/dev/null 2>&1 || \
    fail 'verification dependencies are missing; install cryptography and pytest first'

  (
    cd -- "$testing_target"
    echo "+ $python_bin -m pytest tests"
    "$python_bin" -m pytest tests
    echo "+ $python_bin -m src --scenario all --config config/default.json --seed $SIMULATION_SEED --output logs/verification"
    "$python_bin" -m src \
      --scenario all \
      --config config/default.json \
      --seed "$SIMULATION_SEED" \
      --output logs/verification
  )

  write_manifest "$legal_target" "$temp_dir/legal-after.sha256"
  cmp -s "$temp_dir/legal-before.sha256" "$temp_dir/legal-after.sha256" || \
    fail "the legal copy changed during testing"
  echo "Verification passed; the legal copy remained unchanged."
fi

echo "Legal copy:   $legal_target"
echo "Testing copy: $testing_target"

if $create_zip; then
  if [[ -e $zip_target ]]; then
    $force || fail "ZIP đã tồn tại; đặt FORCE_REPLACE=true để thay thế: $zip_target"
    rm -f -- "$zip_target"
  fi
  (
    cd -- "$(dirname -- "$legal_target")"
    "$python_bin" -m zipfile -c "$zip_target" "$submission_name"
  )
  echo "Submission ZIP: $zip_target"
fi
