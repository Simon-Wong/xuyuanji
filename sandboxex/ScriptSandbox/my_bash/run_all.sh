#!/bin/bash
# 这是一个测试脚本，用于执行所有脚本

#在脚本里用 $(dirname "$0") 获取脚本自身所在的目录，再拼接子脚本路径，这样无论工作目录是什么都能正确找到同级脚本。
#这是 Shell 脚本开发的标准最佳实践。

#vscode的默认是windows的换行符，需要去掉换行符才能在linux上运行
#右下角设置为LF
#用 sed 直接去掉行尾的 \r
#sed -i 's/\r$//' *.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "即将执行run1.sh脚本"
bash "$SCRIPT_DIR/run1.sh"

echo "即将执行run2.sh脚本"
bash "$SCRIPT_DIR/run2.sh"
