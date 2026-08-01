# main.py
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import greet

if __name__ == "__main__":
    print(greet("TSAgent"))
