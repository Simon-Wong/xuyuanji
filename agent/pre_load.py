from tools import load_tools_from_folder
all_tools,tool_map = load_tools_from_folder()

#print("Loaded tools:", [t.name for t in all_tools])

tool_names = list(tool_map.keys())

product_tools=[]
if "write_file" in tool_names and "edit_file_lines" in tool_names:
    product_tools=["write_file","edit_file_lines"]