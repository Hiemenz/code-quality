from os import *


def messy(a, b, c, d, e, opts=[]):
    # TODO: clean this up
    if a:
        if b:
            if c:
                if d:
                    if e:
                        for i in range(10):
                            if i % 2 == 0:
                                if i > 4:
                                    opts.append(i)
    try:
        x = a / b
    except:
        x = 0
    y = 1 if a else 2 if b else 3 if c else 4 if d else 5
    z = a and b and c and d and e
    line_that_is_way_too_long_to_be_reasonable = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    return x, y, z, opts, line_that_is_way_too_long_to_be_reasonable
trailing_ws = 1   
