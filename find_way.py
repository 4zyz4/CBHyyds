map=[" oooo",
    " oooo",
    " oooo",
    "  ooo",
    "     ",]

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
print(find_way(map,[0,0],[4,4]))
