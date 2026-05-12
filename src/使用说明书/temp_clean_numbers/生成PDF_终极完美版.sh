#!/bin/bash

# OpenArmX 使用说明书 PDF 生成脚本（终极完美版）
# 使用include-before-body确保封面完整独立
# 作者: 成都长数机器人有限公司

echo "======================================"
echo "OpenArmX 使用说明书 PDF 生成工具"
echo "版本: 终极完美版"
echo "======================================"

# 设置工作目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 输出文件名
COVER_TEX="封面.tex"
CONTENT_FILE="内容.md"
OUTPUT_FILE="OpenArmX_使用说明书_完整版.md"
PDF_FILE="OpenArmX_使用说明书_完整版.pdf"

# 清理旧文件
echo "[1/5] 清理旧文件..."
rm -f "$COVER_TEX" "$CONTENT_FILE" "$OUTPUT_FILE" "$PDF_FILE"

# 1. 创建独立的封面TEX文件
echo "[2/5] 创建封面..."
cat > "$COVER_TEX" << 'EOF'
\thispagestyle{empty}
\begin{center}
\vspace*{5cm}

{\Huge \textbf{OpenArmX 双臂机器人系统}}

\vspace{1cm}

{\Large \textbf{使用说明书}}

\vspace{0.5cm}

{\large User Manual}

\vspace{3cm}

{\large 产品型号: OpenArmX Bimanual Robot System}

{\large 文档版本: Version 1.0}

\vspace{4cm}

{\Large \textbf{成都长数机器人有限公司}}

{\normalsize Chengdu Changshu Robotics Co., Ltd.}

\vspace{1cm}

openarmrobot@gmail.com

+86-17746530375

https://openarmx.com/

天津经济技术开发区西区新业八街11号华诚机械厂

\vspace{2cm}

发布日期: 2025年10月

适用系统: ROS2 Humble / Ubuntu 22.04 LTS

\end{center}

\newpage
\thispagestyle{empty}

\section*{版权声明}

本使用说明书的版权归\textbf{成都长数机器人有限公司}所有。

\subsection*{使用许可}

\begin{itemize}
\item [√] \textbf{允许} 用户自由阅读、学习和内部培训使用
\item [√] \textbf{允许} 在合法购买产品后复制备份本文档
\item [×] \textbf{禁止} 未经授权的商业性分发或转售
\item [×] \textbf{禁止} 删除或修改文档中的版权信息
\item [×] \textbf{禁止} 将本文档用于任何非法目的
\end{itemize}

\subsection*{免责声明}

\begin{enumerate}
\item 本文档内容如有更新,恕不另行通知,请访问官网获取最新版本
\item 成都长数机器人有限公司对因使用本文档而产生的任何直接或间接损失不承担责任
\item 使用本产品前,请务必完整阅读安全声明章节
\item 如因操作不当造成的人身伤害或财产损失,责任由使用者自行承担
\end{enumerate}

\subsection*{技术支持}

如需技术支持或发现文档错误,请通过以下方式联系我们:

\begin{itemize}
\item 技术支持邮箱: openarmrobot@gmail.com
\item 技术支持电话/微信: +86-17746530375
\item 在线文档: https://openarmx.com/
\end{itemize}

\vspace{2cm}

\textbf{Copyright © 2025 Chengdu Changshu Robotics Co., Ltd. All Rights Reserved.}

\newpage

EOF

echo "✓ 封面创建完成"

# 2. 创建正文内容（不包括封面，不包括手动目录）
echo "[3/5] 合并正文内容..."

# 初始化内容文件
> "$CONTENT_FILE"

# 处理所有章节
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

        echo "\\newpage" >> "$CONTENT_FILE"
        echo "" >> "$CONTENT_FILE"

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
                >> "$CONTENT_FILE"
            echo "" >> "$CONTENT_FILE"
        done
    fi
done

echo "✓ 正文内容合并完成 (共 $(wc -l < "$CONTENT_FILE") 行)"

# 4. 生成 PDF - 使用include-before-body插入封面
echo "[4/5] 生成 PDF（使用独立封面）..."

pandoc "$CONTENT_FILE" \
    -o "$PDF_FILE" \
    --pdf-engine=xelatex \
    --include-before-body="$COVER_TEX" \
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

# 清理临时文件
echo "[5/5] 清理临时文件..."
rm -f "$COVER_TEX" "$CONTENT_FILE"

if [ -f "$PDF_FILE" ]; then
    echo "✓ PDF 生成成功!"
    echo ""
    echo "生成结果:"
    echo "  文件: $PDF_FILE"
    echo "  大小: $(du -h "$PDF_FILE" | cut -f1)"
    if command -v pdfinfo &> /dev/null; then
        pages=$(pdfinfo "$PDF_FILE" 2>/dev/null | grep "Pages:" | awk '{print $2}')
        [ -n "$pages" ] && echo "  页数: $pages"
    fi
    echo ""
    echo "======================================"
    echo "完成! 文档结构:"
    echo "  第1-2页: 完整封面和版权声明"
    echo "  第3页: 自动目录（pandoc生成）"
    echo "  后续: 正文章节（带编号）"
    echo "======================================"
else
    echo "✗ PDF生成失败"
    exit 1
fi
