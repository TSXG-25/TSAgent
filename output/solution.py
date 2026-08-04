def max_active_segments(s):
    if not s:
        return 0

    NEG = -10**9
    # state: 0 = not flipped yet, 1 = inside flipped interval, 2 = flipped interval ended
    dp = [[NEG, NEG] for _ in range(3)]

    b = ord(s[0]) - 48
    dp[0][b] = b
    dp[1][1 - b] = 1 - b

    for ch in s[1:]:
        b = ord(ch) - 48
        ndp = [[NEG, NEG] for _ in range(3)]

        for state in range(3):
            for prev_bit in (0, 1):
                cur = dp[state][prev_bit]
                if cur < 0:
                    continue

                if state == 0:
                    nb = b
                    add = 1 if nb == 1 and prev_bit == 0 else 0
                    if cur + add > ndp[0][nb]:
                        ndp[0][nb] = cur + add

                if state == 0 or state == 1:
                    nb = 1 - b
                    add = 1 if nb == 1 and prev_bit == 0 else 0
                    if cur + add > ndp[1][nb]:
                        ndp[1][nb] = cur + add

                if state == 1 or state == 2:
                    nb = b
                    add = 1 if nb == 1 and prev_bit == 0 else 0
                    if cur + add > ndp[2][nb]:
                        ndp[2][nb] = cur + add

        dp = ndp

    return max(max(row) for row in dp)


if __name__ == "__main__":
    import sys
    s = sys.stdin.readline().strip()
    print(max_active_segments(s))