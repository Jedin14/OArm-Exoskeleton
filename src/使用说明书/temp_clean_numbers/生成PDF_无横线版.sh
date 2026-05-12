#!/bin/bash

# OpenArmX 使用说明书 PDF 生成脚本（无多余横线版本）
# 作者: 成都长数机器人有限公司
# 日期: 2025-10-29

echo "======================================"
echo "OpenArmX 使用说明书 PDF 生成工具"
echo "版本: 无横线优化版"
echo "======================================"

# 设置颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 检查依赖
echo -e "${YELLOW}[1/5] 检查系统依赖...${NC}"
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
echo -e "${YELLOW}[2/5] 清理旧文件...${NC}"
rm -f "$OUTPUT_FILE" "$PDF_FILE"
echo -e "${GREEN}✓ 清理完成${NC}"

# 合并 Markdown 文件
echo -e "${YELLOW}[3/5] 合并 Markdown 文件...${NC}"

# 添加封面（移除 YAML front matter 中的 ---）
echo "添加封面..."
sed '/^---$/d' "00_封面_新.md" > "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# 添加目录（移除页面分隔符）
echo "添加目录..."
grep -v "page-break-after" "01_目录.md" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"
echo "\\newpage" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

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

for chapter in "${chapters[@]}"; do
    if [ -d "$chapter" ]; then
        echo "处理章节: $chapter"

        # 添加章节分页
        echo "\\newpage" >> "$OUTPUT_FILE"
        echo "" >> "$OUTPUT_FILE"

        # 按文件名排序处理章节内的所有 md 文件
        find "$chapter" -name "*.md" -type f | sort | while read -r file; do
            echo "  - 添加: $file"

            # 处理文件内容，移除可能导致横线的元素
            sed -e '/^---$/d' \
                -e '/^___$/d' \
                -e '/^\*\*\*$/d' \
                -e 's/<div style="page-break-after: always;"><\/div>/\\newpage/g' \
                "$file" >> "$OUTPUT_FILE"

            echo "" >> "$OUTPUT_FILE"
        done
    fi
done

echo -e "${GREEN}✓ 文件合并完成${NC}"
echo -e "${GREEN}  生成文件: $OUTPUT_FILE${NC}"

# 统计信息
total_lines=$(wc -l < "$OUTPUT_FILE")
echo -e "${GREEN}  总行数: $total_lines${NC}"

# 生成 PDF
echo -e "${YELLOW}[4/5] 生成 PDF 文件...${NC}"
echo "这可能需要几分钟时间，请耐心等待..."

# 使用 pandoc 生成 PDF，优化选项避免多余横线
pandoc "$OUTPUT_FILE" \
    -o "$PDF_FILE" \
    --pdf-engine=xelatex \
    --toc \
    --toc-depth=3 \
    --number-sections \
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
    --metadata title="OpenArmX 双臂机器人系统使用说明书" \
    --metadata author="成都长数机器人有限公司" \
    --metadata date="2025年10月" \
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

    # 统计页数（如果安装了 pdfinfo）
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
echo -e "${YELLOW}[5/5] 清理临时文件...${NC}"
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
echo "说明:"
echo "  - 已移除所有可能导致多余横线的分隔符"
echo "  - 已优化表格和代码块样式"
echo "  - 包含完整的封面和目录"
echo "  - 使用中文字体优化排版"
echo ""
echo "如需修改样式，请编辑脚本中的 pandoc 参数"
echo ""
