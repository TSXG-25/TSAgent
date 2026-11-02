# todos.py

def add_todo(task):
    with open('todos.txt', 'a') as file:
        file.write(task + '\n')

def list_todos():
    try:
        with open('todos.txt', 'r') as file:
            tasks = file.readlines()
            if not tasks:
                print("没有待办事项。")
            else:
                print("待办事项:")
                for i, task in enumerate(tasks, 1):
                    print(f"{i}. {task.strip()}")
    except FileNotFoundError:
        print("没有找到待办事项文件。")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python todos.py [添加任务 | 列出任务]")
    elif sys.argv[1] == "添加任务":
        if len(sys.argv) < 3:
            print("请提供任务内容。")
        else:
            task = ' '.join(sys.argv[2:])
            add_todo(task)
            print(f"任务 '{task}' 已添加。")
    elif sys.argv[1] == "列出任务":
        list_todos()
    else:
        print("无效的命令。用法: python todos.py [添加任务 | 列出任务]")