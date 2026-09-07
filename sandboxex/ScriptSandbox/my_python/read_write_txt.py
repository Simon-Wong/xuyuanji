#读取txt的内容并写入另一个文件

import sys

def read_write_txt(pathfile_read:str, pathfile_write:str):
    with open(pathfile_read, 'r') as f:
        content = f.read()
        with open(pathfile_write, 'w') as f:
            f.write(content)



if __name__ == '__main__':
    #路径由命令行传入

    pathfile_read = sys.argv[1]
    pathfile_write = sys.argv[2]

    read_write_txt(pathfile_read, pathfile_write)