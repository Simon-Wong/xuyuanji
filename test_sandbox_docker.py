def test_BatchShellSandbox():
    from sandboxex import BatchShellSandbox
    sandbox = BatchShellSandbox(timeout=30)
    code, stdout, stderr = sandbox.run([
        "echo 'step 1: generate data'",
        "echo 'hello world' > data.txt",
        "cat data.txt",
        "python -c 'print(\"step 2: python executed\")'",
    ])

    print("返回码:", code)
    print("标准输出:\n", stdout)
    print("标准错误:\n", stderr)

def test_HttpStatefulSandbox():
    from sandboxex import HttpStatefulSandbox
    sandbox = HttpStatefulSandbox()
    if not sandbox.start():
        print("沙箱启动失败")
        exit(1)

    # 第一段代码：定义变量
    r1 = sandbox.execute("x = 100\ny = 200\nprint('variables set')")
    print("Step1:", r1)

    # 第二段代码：使用上一步的变量（内存共享）
    r2 = sandbox.execute("print(x + y)")
    print("Step2:", r2)  # 输出 300

    sandbox.destroy()

def test_InteractivePythonSandbox():
    from sandboxex import PersistentSandbox, InteractivePythonSandbox
    # 先启动长驻容器
    sandbox = PersistentSandbox()
    sandbox.start()

    # 在容器内启动交互式 Python
    py_sandbox = InteractivePythonSandbox(sandbox.container_name)
    py_sandbox.start()

    # 第一次执行：定义变量
    out1 = py_sandbox.execute("a = 42\nb = 58")
    print("Out1:", out1)

    # 第二次执行：使用变量（内存共享）
    out2 = py_sandbox.execute("print(a + b)")
    print("Out2:", out2)  # 输出 100

    # 第三次执行：定义函数
    str_func ='''
def hello(name):
    return 'Hello, ' + name
'''
    out3 = py_sandbox.execute(str_func)
    print("Out3:", out3)

    # 第四次执行：调用函数
    out4 = py_sandbox.execute("print(hello('World'))")
    print("Out4:", out4)  # 输出 Hello, World

    py_sandbox.stop()
    sandbox.destroy()


def test_PersistentSandbox_1():
    from sandboxex import PersistentSandbox
#验证docker内持久化
    sandbox = PersistentSandbox(exec_timeout=15)
    print(f"Output dir: {sandbox.output_dir}")
    sandbox.start()

    # 第1次执行：生成文件到 /output（预挂载，宿主机立即可见）
    code, out, err = sandbox.exec("echo 'data from test1' > /output/result_cat.txt")
    print("Test1 Step1:", code)
    print("Test1 Step1:", out)
    print("Test1 Step1:", err)
    print('\n')
    # 第2次执行：从 /output 读取文件（验证持久化）
    code, out, err = sandbox.exec("cat /output/result_cat.txt")
    print("Test1 Step2:", code)
    print("Test1 Step2:", out)
    print("Test1 Step2:", err)
    print('='*50)
    
    # 销毁沙箱
    sandbox.destroy()

def test_PersistentSandbox_2():
    from sandboxex import PersistentSandbox
    import os
# 方式：指定持久目录（需用当前用户可写的路径）
    sandbox = PersistentSandbox(output_dir="./")#注意：开头设置了ROOT_DIR
    print(f"Output dir: {sandbox.output_dir}")

    if not sandbox.start():
        print("Test2: 启动失败")
        return

    code, out, err = sandbox.exec("echo 'data from test2' > /output/result_pipe.txt")
    print("Test2 Step1:", code)
    print("Test2 Step1:", out)
    print("Test2 Step1:", err)
    print('\n')

    sandbox.destroy()

    # 验证：直接从宿主机读取
    host_result = os.path.join(os.path.abspath("./"), "result_pipe.txt")
    print("Host result file:", host_result)
    if os.path.exists(host_result):
        with open(host_result) as f:
            print("Content:", f.read().strip())
    print('='*50)

def test_PersistentSandbox_3():
    from sandboxex import PersistentSandbox
    import os
# 方式：事后拷贝（非 /output 路径）
    sandbox = PersistentSandbox()
    print(f"Output dir: {sandbox.output_dir}")

    sandbox.start()

    code, out, err = sandbox.exec("echo 'data from test3' > /output/result_copy.txt")
    print("Test3 Step1:", code)
    print("Test3 Step1:", out)
    print("Test3 Step1:", err)
    print('\n')

    sandbox.copy_from_container("/output/result_copy.txt", "./result_copy.txt")    

    # 销毁沙箱
    sandbox.destroy()

    # 验证：直接从宿主机读取
    host_result = os.path.join(os.path.abspath("./"), "result_copy.txt")
    print("Host result file:", host_result)
    if os.path.exists(host_result):
        with open(host_result) as f:
            print("Content:", f.read().strip())
    print('='*50)

def test_ScriptSandbox_bash():
    from sandboxex import ScriptSandbox
    # 本地my_bash 目录下放好 run_all.sh 和所有子脚本
    TEST_DIR_LINUX="/home/thbytwo/testCode/xuyuanji/sandboxex/ScriptSandbox/my_bash"
    
    sandbox = ScriptSandbox(timeout=60)
    code, out, err = sandbox.run_script(
        host_scripts_dir=TEST_DIR_LINUX,  # 本地目录
        entry_script="run_all.sh"         # 脚本在本地目录里的名字
    )

    print("1返回码:", code)
    print("1标准输出:\n", out)
    print("1标准错误:\n", err)

    # windows的test_my_scripts目录下放好 run_all.sh 和所有子脚本
    TEST_DIR_WINDOWS="/mnt/d/testCode/test_my_scripts"

    sandbox = ScriptSandbox(timeout=60)
    code, out, err = sandbox.run_script(
        host_scripts_dir=TEST_DIR_WINDOWS, #TEST_DIR_LINUX,  # 本地目录
        entry_script="run_all.sh"         # 脚本在本地目录里的名字
    )

    print("2返回码:", code)
    print("2标准输出:\n", out)
    print("2标准错误:\n", err)

def test_ScriptSandbox_python():
    from sandboxex import ScriptSandbox
    sandbox = ScriptSandbox(timeout=60)

    # # 1. 基础用法：执行单个 Python 脚本
    # code, out, err = sandbox.run_python_script(
    #     host_scripts_dir="/home/thbytwo/testCode/xuyuanji/sandboxex/ScriptSandbox/my_python",
    #     entry_script="print_hello.py"
    # )
    # print("1返回码:", code)
    # print("1标准输出:\n", out)
    # print("1标准错误:\n", err)

    # 2. 带脚本参数 + 额外挂载数据目录
    code, out, err = sandbox.run_python_script(
        host_scripts_dir="/home/thbytwo/testCode/xuyuanji/sandboxex/ScriptSandbox/my_python",
        host_data_dir="/home/thbytwo/testCode/xuyuanji/sandboxex/ScriptSandbox/my_python",
        entry_script="read_write_txt.py",
        script_args=[
            "{data}/some.txt",        # 输入文件：主机 my_python/some.txt → 容器 /data/some.txt
            "{work}/some_w.txt",      # 输出文件：写到容器 /work（tmpfs 可写）
        ]
    )

    print("2返回码:", code)
    print("2标准输出:\n", out)
    print("2标准错误:\n", err)

if __name__ == "__main__":
    #test_BatchShellSandbox()
    #test_HttpStatefulSandbox()
    #test_InteractivePythonSandbox()
    #test_PersistentSandbox_1()
    test_PersistentSandbox_2()
    #test_PersistentSandbox_3()
    #test_ScriptSandbox_bash()
    #test_ScriptSandbox_python()
    