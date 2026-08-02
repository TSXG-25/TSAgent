def max_active_segments(s):
    # 原始字符串中连续1段数
    def count_segments(t):
        cnt = 0
        i = 0
        n = len(t)
        while i < n:
            if t[i] == '1':
                cnt += 1
                while i < n and t[i] == '1':
                    i += 1
            else:
                i += 1
        return cnt

    original = count_segments(s)
    
    # 第一步：将所有被0包围的连续1块变为0
    n = len(s)
    chars = list(s)
    i = 0
    while i < n:
        if s[i] == '1':
            j = i
            while j < n and s[j] == '1':
                j += 1
            left_ok = (i > 0 and s[i-1] == '0')
            right_ok = (j < n and s[j] == '0')
            if left_ok and right_ok:
                for k in range(i, j):
                    chars[k] = '0'
            i = j
        else:
            i += 1

    # 第二步：将所有被1包围的连续0块变为1（基于chars）
    step2 = chars[:]
    i = 0
    while i < n:
        if chars[i] == '0':
            j = i
            while j < n and chars[j] == '0':
                j += 1
            left_ok = (i > 0 and chars[i-1] == '1')
            right_ok = (j < n and chars[j] == '1')
            if left_ok and right_ok:
                for k in range(i, j):
                    step2[k] = '1'
            i = j
        else:
            i += 1

    final = ''.join(step2)
    after_operation = count_segments(final)
    return max(original, after_operation)

if __name__ == "__main__":
    import sys
    s = sys.stdin.readline().strip()
    print(max_active_segments(s))
