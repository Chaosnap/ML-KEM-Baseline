#!/usr/bin/env python3
"""Merge duplicate scopes in a VCD header (in place).

Icarus writes one copy of the top-level scope for every $dumpvars call, so a
testbench that dumps a few selected scopes shows the same module several
times in a waveform viewer. This rewrites the header so each scope path
appears once, with all of its signals and sub-scopes. Signal identifiers and
the value-change data are unchanged (the body is copied byte for byte).

Usage: python3 tb/vcd_merge_scopes.py <file.vcd> [...]
"""

import os
import shutil
import sys


class Scope:
    def __init__(self, header):
        self.header = header          # "$scope module name $end" line
        self.vars = []                # "$var ..." lines, in order
        self.children = {}            # header line → Scope, insertion-ordered

    def emit(self, out):
        out.append(self.header)
        out.extend(self.vars)
        for child in self.children.values():
            child.emit(out)
        out.append("$upscope $end\n")


def merge(path):
    tmp = path + ".tmp"
    with open(path, "r", encoding="ascii", errors="replace", newline="") as src:
        preamble, roots, stack = [], {}, []
        for line in src:
            token = line.split(None, 1)[0] if line.strip() else ""
            if token == "$scope":
                key = " ".join(line.split())
                siblings = stack[-1].children if stack else roots
                scope = siblings.setdefault(key, Scope(key + "\n"))
                stack.append(scope)
            elif token == "$upscope":
                stack.pop()
            elif token == "$var" and stack:
                if line not in stack[-1].vars:
                    stack[-1].vars.append(line)
            elif token == "$enddefinitions":
                break
            elif not roots:
                preamble.append(line)
        else:
            raise ValueError(f"{path}: no $enddefinitions found")

        header = list(preamble)
        for root in roots.values():
            root.emit(header)
        header.append(line)           # the $enddefinitions line

        with open(tmp, "w", encoding="ascii", newline="") as dst:
            dst.writelines(header)
            shutil.copyfileobj(src, dst, 1 << 20)
    os.replace(tmp, path)
    return len(roots)


if __name__ == "__main__":
    for vcd in sys.argv[1:]:
        n = merge(vcd)
        print(f"{vcd}: header merged, {n} top-level scope(s)")
