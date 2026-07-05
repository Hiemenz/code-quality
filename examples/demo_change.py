def handle(a, b, c, d, opts={}):
    if a:
        if b:
            if c:
                if d:
                    for i in range(10):
                        if i % 2 == 0:
                            opts[i] = i
    try:
        return opts[a]
    except:
        return None
