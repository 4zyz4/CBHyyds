def find_way(map,start,end,way=[],):
    try:
        if start[0]<0 or start[1]<0:
            return []
        if map[start[0]][start[1]] in "oO":
            return []
        if start in way:
            return []
    except:
        return []
    if start==end:
            return [way+[end]]
    return find_way(map,[start[0]+1,start[1]],end,way=way+[start])+find_way(map,[start[0],start[1]+1],end,way=way+[start])+find_way(map,[start[0]-1,start[1]],end,way=way+[start])+find_way(map,[start[0],start[1]-1],end,way=way+[start])

def print_way(map,way,one_by_one=False):
    import time
    import os
    if one_by_one==1:
        for i in way:
            os.system('clear' if os.name=='posix' else 'cls')
            print_way(map,[i])
            time.sleep(0.05)
        time.sleep(1)
        return
    for i in range(len(map)):
            for j in range(len(map[i])):
                if [i,j] in way:
                    print("*",end="")
                else:
                    print(map[i][j],end="")
            print()

map=list(""" 
 ooooooooo
  oooooooo
o  ooooooo
oo  oooooo
ooo oooooo
ooo    ooo
ooo oo ooo
ooo oo ooo
ooo oo ooo
ooo       """.split("\n"))[1:]
a=find_way(map,[0,0],[9,9])
for i in a:
    print_way(map,i)
    print()
enter=input("按ENTER键开始")
for i in a:
    print_way(map,i,one_by_one=1)
