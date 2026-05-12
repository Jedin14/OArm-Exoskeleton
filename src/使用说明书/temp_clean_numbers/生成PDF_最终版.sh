#!/bin/bash

# OpenArmX 使用说明书 PDF 生成脚本（最终版）
# 修复：1. 封面完整  2. 移除手动目录  3. 使用pandoc自动目录  4. 章节编号
# 作者: 成都长数机器人有限公司

echo "======================================"
echo "OpenArmX 使用说明书 PDF 生成工具"
echo "版本: 最终完美版"
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

# 1. 处理封面 - 完整保留，但将"版权声明"等标题降级，避免被编号
echo "处理封面..."
cat "00_封面_新.md" | \
    sed 's/✅/[√]/g; s/❌/[×]/g; s/⚠️/[警告]/g; s/⚠/[警告]/g; s/📧//g; s/📞//g; s/🌐//g' | \
    sed 's/^## 版权声明$/\\section*{版权声明}/g' | \
    sed 's/^### 使用许可$/\\subsection*{使用许可}/g' | \
    sed 's/^### 免责声明$/\\subsection*{免责声明}/g' | \
    sed 's/^### 技术支持$/\\subsection*{技术支持}/g' \
    > "$OUTPUT_FILE"

echo "" >> "$OUTPUT_FILE"

# 2. 跳过手动目录，让pandoc自动生成
echo "跳过手动目录（使用pandoc自动生成）"

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
                sed 's/📹//g; s/🏫//g; s/►//g; s/◄//g; s/✘//g; s/✗//g; s/🟢//g; s/⟳//g; s/👍//g' \
                >> "$OUTPUT_FILE"
            echo "" >> "$OUTPUT_FILE"
        done
    fi
done

echo "✓ 文件合并完成 (共 $(wc -l < "$OUTPUT_FILE") 行)"

# 4. 生成 PDF - 使用pandoc自动目录和章节编号
echo "[3/4] 生成 PDF（自动目录+章节编号）..."

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
    -V toc-title="目录" \
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
    echo "完成! 所有优化:"
    echo "  [√] 封面完整（不分割）"
    echo "  [√] 版权声明不编号"
    echo "  [√] 移除手动目录"
    echo "  [√] Pandoc自动生成目录"
    echo "  [√] 章节自动编号（1, 1.1, 1.1.1...）"
    echo "  [√] emoji显示优化"
    echo "  [√] 无多余横线"
    echo "======================================"
    echo ""
    echo "文档结构:"
    echo "  1. 封面（含版权声明）"
    echo "  2. 自动目录（pandoc生成）"
    echo "  3. 正文章节（带编号）"
    echo ""
else
    echo "✗ PDF生成失败"
    exit 1
fi
