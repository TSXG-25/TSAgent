import json
import os

LEDGER_FILE = 'ledger.json'

def load_ledger():
    if not os.path.exists(LEDGER_FILE):
        return []
    with open(LEDGER_FILE, 'r') as f:
        return json.load(f)

def save_ledger(ledger):
    with open(LEDGER_FILE, 'w') as f:
        json.dump(ledger, f, indent=4)

def add_entry(description, amount, category):
    ledger = load_ledger()
    entry = {
        'description': description,
        'amount': amount,
        'category': category
    }
    ledger.append(entry)
    save_ledger(ledger)

def delete_entry(index):
    ledger = load_ledger()
    if 0 <= index < len(ledger):
        del ledger[index]
        save_ledger(ledger)
        print("记录已删除。")
    else:
        print("无效的索引。")

if __name__ == '__main__':
    description = input("请输入描述: ")
    amount = float(input("请输入金额: "))
    category = input("请输入分类: ")
    add_entry(description, amount, category)
    print("记录已添加。")