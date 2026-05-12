#!/bin/bash

# OpenArmX 使用说明书 PDF 生成脚本（带章节编号版）
# 修复：1. 封面完整性  2. emoji显示问题  3. 添加章节和标题编号
# 作者: 成都长数机器人有限公司

echo "======================================"
echo "OpenArmX 使用说明书 PDF 生成工具"
echo "版本: 带章节编号版"
echo "======================================"

# 设置工作目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 输出文件名
OUTPUT_FILE="OpenArmX_使用说明书_完整版.md"
PDF_FILE="OpenArmX_使用说明书_完整版.pdf"

# 清理旧文件
echo "[1/4] 清理旧文件..."
rm -f "$OUTPUT_FILE" "$PDF_FILE"

# 开始构建
echo "[2/4] 合并 Markdown 文件..."

# 1. 添加封面 - 特殊处理保持LaTeX完整性
echo "处理封面..."
cat "00_封面_新.md" | \
    sed 's/✅/[√]/g; s/❌/[×]/g; s/⚠️/[警告]/g; s/⚠/[警告]/g; s/📧//g; s/📞//g; s/🌐//g' \
    > "$OUTPUT_FILE"

echo "" >> "$OUTPUT_FILE"
echo "\\newpage" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# 2. 添加目录
echo "处理目录..."
cat "01_目录.md" | \
    sed '/^---$/d; /<div style="page-break-after/d' | \
    sed 's/✅/[√]/g; s/❌/[×]/g; s/⚠️/[警告]/g; s/⚠/[警告]/g' \
    >> "$OUTPUT_FILE"

echo "" >> "$OUTPUT_FILE"

# 3. 处理所有章节
chapters=(
    "00_安全声明"
    "01_系统概述"
    "02_环境准备"
    "03_硬件连接与配置"
    "04_电机测试与调试"
    "05_MoveIt运动规划"
    "06_轨迹录制与回放"
    "07_控制器配置"
    "08_高级应用"
    "09_故障排查"
    "10_开发者指南"
    "11_附录"
)

for chapter in "${chapters[@]}"; do
    if [ -d "$chapter" ]; then
        echo "处理章节: $chapter"

        echo "\\newpage" >> "$OUTPUT_FILE"
        echo "" >> "$OUTPUT_FILE"

        find "$chapter" -name "*.md" -type f | sort | while read -r file; do
            echo "  - $file"
            cat "$file" | \
                sed '/^---$/d; /^___$/d; /^\*\*\*$/d; /<div style="page-break-after/d' | \
                sed 's/✅/[√]/g; s/❌/[×]/g; s/⚠️/[警告]/g; s/⚠/[警告]/g; s/❗/[!]/g; s/ℹ️/[i]/g; s/ℹ/[i]/g' | \
                sed 's/📧//g; s/📞//g; s/🌐//g; s/📍//g; s/🤖//g; s/⚡//g; s/🔓//g; s/🔒//g; s/🎯//g; s/🔧//g' | \
                sed 's/🔌//g; s/🪵//g; s/🏥//g; s/🩹//g; s/🚑//g; s/💼//g; s/📄//g; s/💻//g; s/📚//g; s/🏷️//g; s/🏷//g' | \
                sed 's/💰//g; s/☑️/[√]/g; s/☑/[√]/g; s/🔴/[红]/g; s/🟠/[橙]/g; s/🟡/[黄]/g; s/✋/[手]/g' | \
                sed 's/🚶/[人]/g; s/👀/[眼]/g; s/⚙️//g; s/⚙//g; s/₂/2/g; s/🎓//g; s/🔬//g; s/🏭//g; s/💡//g' | \
                sed 's/📦//g; s/☕//g; s/🍳//g; s/🧹//g; s/🎨//g; s/🎭//g; s/🎮//g; s/⭐//g; s/🔜//g; s/💬//g' | \
                sed 's/📹//g; s/🏫//g; s/►//g; s/◄//g; s/✘//g; s/✗//g; s/🟢//g; s/⟳//g; s/👍//g; s/💬//g' \
                >> "$OUTPUT_FILE"
            echo "" >> "$OUTPUT_FILE"
        done
    fi
done

echo "✓ 文件合并完成 (共 $(wc -l < "$OUTPUT_FILE") 行)"

# 4. 生成 PDF - 启用章节编号
echo "[3/4] 生成 PDF（启用章节编号）..."

pandoc "$OUTPUT_FILE" \
    -o "$PDF_FILE" \
    --pdf-engine=xelatex \
    --number-sections \
    --toc \
    --toc-depth=3 \
    -V documentclass=article \
    -V papersize=a4 \
    -V geometry:margin=2.5cm \
    -V mainfont="Noto Serif CJK SC" \
    -V monofont="Noto Sans Mono CJK SC" \
    -V fontsize=11pt \
    -V linestretch=1.5 \
    -V CJKmainfont="Noto Serif CJK SC" \
    --highlight-style=tango \
    -V colorlinks=true \
    -V linkcolor=blue \
    -V urlcolor=blue \
    -V toccolor=black \
    -V secnumdepth=4 \
    -V header-includes="\
        \\usepackage{fancyhdr} \
        \\usepackage{lastpage} \
        \\pagestyle{fancy} \
        \\fancyhf{} \
        \\fancyhead[L]{OpenArmX 使用说明书} \
        \\fancyhead[R]{\\thepage} \
        \\fancyfoot[C]{成都长数机器人有限公司} \
        \\renewcommand{\\headrulewidth}{0.4pt} \
        \\renewcommand{\\footrulewidth}{0.4pt} \
        \\setcounter{secnumdepth}{4} \
        \\setcounter{tocdepth}{3}"

if [ -f "$PDF_FILE" ]; then
    echo "✓ PDF 生成成功!"
    echo ""
    echo "[4/4] 生成结果:"
    echo "  文件: $PDF_FILE"
    echo "  大小: $(du -h "$PDF_FILE" | cut -f1)"
    if command -v pdfinfo &> /dev/null; then
        pages=$(pdfinfo "$PDF_FILE" 2>/dev/null | grep "Pages:" | awk '{print $2}')
        [ -n "$pages" ] && echo "  页数: $pages"
    fi
    echo ""
    echo "======================================"
    echo "完成! 已修复:"
    echo "  [√] 封面完整性"
    echo "  [√] emoji显示问题"
    echo "  [√] 多余横线问题"
    echo "  [√] 章节和标题自动编号"
    echo "======================================"
    echo ""
    echo "说明:"
    echo "  - 启用了 --number-sections 自动编号"
    echo "  - 章节层级: 1. 一级章节"
    echo "               1.1 二级标题"
    echo "               1.1.1 三级标题"
    echo "               1.1.1.1 四级标题"
    echo "  - 目录自动生成，包含所有编号"
    echo ""
else
    echo "✗ PDF生成失败"
    exit 1
fi
