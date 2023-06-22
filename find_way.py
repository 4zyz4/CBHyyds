import os
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
    if one_by_one==1:
        for i in way:
            os.system('clear' if os.name=='posix' else 'cls')
            print_way(map,[i])
            time.sleep(0.05)
        time.sleep(2)
        return
    for i in range(len(map)):
            for j in range(len(map[i])):
                if [i,j] in way:
                    print("*",end=" ")
                else:
                    print(map[i][j],end=" ")
            print()
def print_map(map,width,pots=[]):
    os.system('clear' if os.name=='posix' else 'cls')
    for i in range(len(map)):
        print(i+1," "*(len(str(len(map)))-len(str(i+1))+1),"|",sep="",end="")
        for j in range(len(map[i])):
            if [i,j] in pots:
                print("*",end=" ")
            else:
                print(map[i][j],end=" ")
        print()
    print(" "*(len(str(len(map)))+1)+"-"*(len(map[0])*2))
    for i in range(len(str(len(map)))):
        print(" "*(len(str(len(map)))+2),end="")
        for j in range(width):
            try:
                print(str(j+1)[i],end=" ")
            except:
                print(" ",end=" ")
        print()

print("迷宫寻路")
width=int(input("输入迷宫宽度（左右）："))
lenth=int(input("输入迷宫高度（上下）："))
print("粘贴迷宫")
map=[]
for i in range(lenth):
     map+=[input().replace("\xa0"," ")]
print_map(map,width)

start=[]
end=[]
start.append(int(input("输入起点行数:"))-1)
start.append(int(input("输入起点列数:"))-1)
print_map(map,width,pots=[start])
print("*是启点的位置")
end.append(int(input("输入终点行数:"))-1)
end.append(int(input("输入终点列数:"))-1)
print_map(map,width,pots=[end])
print("*是终点的位置")
enter=input("按ENTER键开始寻路")
os.system('clear' if os.name=='posix' else 'cls')
w=find_way(map,start,end)
print("找到以下路线")
while 1:
    for i in range(len(w)):
        print("第"+str(i+1)+"条：")
        print_way(map,w[i])
        print()
    i=int(input("输入要演示的路线编号："))-1
    print_way(map,w[i],one_by_one=1)
    os.system('clear' if os.name=='posix' else 'cls')
