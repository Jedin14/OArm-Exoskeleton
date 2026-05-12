#!/bin/bash

# OpenArmX 使用说明书 PDF 生成脚本（终极优化版）
# 修复：1. 封面完整性  2. emoji显示问题
# 作者: 成都长数机器人有限公司
# 日期: 2025-10-29

echo "======================================"
echo "OpenArmX 使用说明书 PDF 生成工具"
echo "版本: 终极优化版"
echo "======================================"

# 设置颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 检查依赖
echo -e "${YELLOW}[1/6] 检查系统依赖...${NC}"
if ! command -v pandoc &> /dev/null; then
    echo -e "${RED}错误: 未安装 pandoc${NC}"
    echo "请运行: sudo apt-get install pandoc"
    exit 1
fi

if ! command -v xelatex &> /dev/null; then
    echo -e "${RED}错误: 未安装 texlive-xetex${NC}"
    echo "请运行: sudo apt-get install texlive-xetex texlive-fonts-recommended texlive-lang-chinese"
    exit 1
fi

echo -e "${GREEN}✓ 依赖检查通过${NC}"

# 设置工作目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 输出文件名
OUTPUT_FILE="OpenArmX_使用说明书_完整版.md"
PDF_FILE="OpenArmX_使用说明书_完整版.pdf"

# 清理旧文件
echo -e "${YELLOW}[2/6] 清理旧文件...${NC}"
rm -f "$OUTPUT_FILE" "$PDF_FILE"
echo -e "${GREEN}✓ 清理完成${NC}"

# emoji清理函数
clean_emoji() {
    local input_file="$1"

    # 替换常见的emoji为文字
    sed -e 's/✅/[√]/g' \
        -e 's/❌/[×]/g' \
        -e 's/⚠️/[警告]/g' \
        -e 's/⚠/[警告]/g' \
        -e 's/❗/[!]/g' \
        -e 's/ℹ️/[i]/g' \
        -e 's/ℹ/[i]/g' \
        -e 's/📧//g' \
        -e 's/📞//g' \
        -e 's/🌐//g' \
        -e 's/📍//g' \
        -e 's/🤖//g' \
        -e 's/⚡//g' \
        -e 's/🔓//g' \
        -e 's/🔒//g' \
        -e 's/🎯//g' \
        -e 's/🔧//g' \
        -e 's/🔌//g' \
        -e 's/🪵//g' \
        -e 's/🏥//g' \
        -e 's/🩹//g' \
        -e 's/🚑//g' \
        -e 's/💼//g' \
        -e 's/📄//g' \
        -e 's/💻//g' \
        -e 's/📚//g' \
        -e 's/🏷️//g' \
        -e 's/🏷//g' \
        -e 's/💰//g' \
        -e 's/☑️/[√]/g' \
        -e 's/☑/[√]/g' \
        -e 's/🔴/[红]/g' \
        -e 's/🟠/[橙]/g' \
        -e 's/🟡/[黄]/g' \
        -e 's/✋/[手]/g' \
        -e 's/🚶/[人]/g' \
        -e 's/👀/[眼]/g' \
        -e 's/⚙️//g' \
        -e 's/⚙//g' \
        "$input_file"
}

# 合并 Markdown 文件
echo -e "${YELLOW}[3/6] 合并 Markdown 文件...${NC}"

# 处理封面 - 特殊处理，保持LaTeX格式完整
echo "添加封面..."
{
    # 只保留LaTeX部分的封面内容，移除YAML header和多余分隔符
    sed -n '/\\begin{center}/,/\\end{center}/p' "00_封面_新.md"
    echo ""
    echo "\\newpage"
    echo ""
    # 添加版权声明部分
    sed -n '/## 版权声明/,/\\newpage/p' "00_封面_新.md" | sed '/^---$/d'
    echo ""
} | clean_emoji > "$OUTPUT_FILE"

# 添加目录（作为普通内容，不使用pandoc自动生成）
echo "添加目录..."
{
    echo "\\newpage"
    echo ""
    grep -v "page-break-after" "01_目录.md" | sed '/^---$/d'
    echo ""
    echo "\\newpage"
    echo ""
} | clean_emoji >> "$OUTPUT_FILE"

# 按顺序合并各章节
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

echo -e "${YELLOW}[4/6] 处理章节内容并清理emoji...${NC}"
for chapter in "${chapters[@]}"; do
    if [ -d "$chapter" ]; then
        echo "处理章节: $chapter"

        # 添加章节分页
        echo "\\newpage" >> "$OUTPUT_FILE"
        echo "" >> "$OUTPUT_FILE"

        # 按文件名排序处理章节内的所有 md 文件
        find "$chapter" -name "*.md" -type f | sort | while read -r file; do
            echo "  - 添加: $file"

            # 处理文件内容：移除分隔符 + 清理emoji
            {
                sed -e '/^---$/d' \
                    -e '/^___$/d' \
                    -e '/^\*\*\*$/d' \
                    -e 's/<div style="page-break-after: always;"><\/div>/\\newpage/g' \
                    "$file"
                echo ""
            } | clean_emoji >> "$OUTPUT_FILE"
        done
    fi
done

echo -e "${GREEN}✓ 文件合并完成${NC}"
echo -e "${GREEN}  生成文件: $OUTPUT_FILE${NC}"

# 统计信息
total_lines=$(wc -l < "$OUTPUT_FILE")
echo -e "${GREEN}  总行数: $total_lines${NC}"

# 生成 PDF
echo -e "${YELLOW}[5/6] 生成 PDF 文件...${NC}"
echo "这可能需要几分钟时间，请耐心等待..."

# 使用 pandoc 生成 PDF
pandoc "$OUTPUT_FILE" \
    -o "$PDF_FILE" \
    --pdf-engine=xelatex \
    --toc-depth=3 \
    -V documentclass=article \
    -V papersize=a4 \
    -V geometry:margin=2.5cm \
    -V mainfont="Noto Serif CJK SC" \
    -V monofont="Noto Sans Mono CJK SC" \
    -V fontsize=11pt \
    -V linestretch=1.5 \
    -V CJKmainfont="Noto Serif CJK SC" \
    -V links-as-notes=false \
    --highlight-style=tango \
    -V colorlinks=true \
    -V linkcolor=blue \
    -V urlcolor=blue \
    -V toccolor=black \
    -V header-includes="\
        \\usepackage{fancyhdr} \
        \\usepackage{lastpage} \
        \\usepackage{booktabs} \
        \\usepackage{longtable} \
        \\usepackage{array} \
        \\pagestyle{fancy} \
        \\fancyhf{} \
        \\fancyhead[L]{OpenArmX 使用说明书} \
        \\fancyhead[R]{\\thepage\\ / \\pageref{LastPage}} \
        \\fancyfoot[C]{成都长数机器人有限公司} \
        \\renewcommand{\\headrulewidth}{0.4pt} \
        \\renewcommand{\\footrulewidth}{0.4pt} \
        \\setlength{\\parskip}{6pt} \
        \\usepackage[normalem]{ulem} \
        \\usepackage{xcolor}" \
    2>&1 | tee pandoc_log.txt

# 检查 PDF 生成结果
if [ -f "$PDF_FILE" ]; then
    echo -e "${GREEN}✓ PDF 生成成功!${NC}"
    echo -e "${GREEN}  输出文件: $PDF_FILE${NC}"

    # 显示文件大小
    file_size=$(du -h "$PDF_FILE" | cut -f1)
    echo -e "${GREEN}  文件大小: $file_size${NC}"

    # 统计页数
    if command -v pdfinfo &> /dev/null; then
        page_count=$(pdfinfo "$PDF_FILE" 2>/dev/null | grep "Pages:" | awk '{print $2}')
        if [ -n "$page_count" ]; then
            echo -e "${GREEN}  总页数: $page_count${NC}"
        fi
    fi
else
    echo -e "${RED}✗ PDF 生成失败${NC}"
    echo -e "${YELLOW}请查看 pandoc_log.txt 获取详细错误信息${NC}"
    exit 1
fi

# 完成
echo -e "${YELLOW}[6/6] 清理临时文件...${NC}"
echo "保留合并的 Markdown 文件供参考: $OUTPUT_FILE"
echo "清理日志文件..."
rm -f pandoc_log.txt

echo ""
echo "======================================"
echo -e "${GREEN}PDF 生成完成!${NC}"
echo "======================================"
echo ""
echo "生成的文件:"
echo "  📄 Markdown: $OUTPUT_FILE"
echo "  📕 PDF: $PDF_FILE"
echo ""
echo "优化说明:"
echo "  [√] 封面完整性已修复"
echo "  [√] 所有emoji已替换为文字符号"
echo "  [√] 移除所有多余横线"
echo "  [√] 优化中文排版"
echo ""
