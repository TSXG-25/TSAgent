output/huobi_calculator.py
```python
def calculate_conversion(amount, exchange_rate):
    converted_amount = amount * exchange_rate
    return converted_amount

def main():
    try:
        amount = float(input("请输入金额: "))
        exchange_rate = float(input("请输入汇率: "))
        result = calculate_conversion(amount, exchange_rate)
        print(f"转换后的金额为: {result}")
    except ValueError:
        print("请输入有效的数字。")

if __name__ == "__main__":
    main()