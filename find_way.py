import os
def find_way(mapp,start,end,way=[],):#核心代码，只有13行
    try:
        if start[0]<0 or start[1]<0:
            return []
        if mapp[start[0]][start[1]] in "oO":
            return []
        if start in way:
            return []
    except:
        return []
    if start==end:
            return [way+[end]]
    return find_way(mapp,[start[0]+1,start[1]],end,way=way+[start])+find_way(mapp,[start[0],start[1]+1],end,way=way+[start])+find_way(mapp,[start[0]-1,start[1]],end,way=way+[start])+find_way(mapp,[start[0],start[1]-1],end,way=way+[start])
def print_way(mapp,way,one_by_one=False):#从这里开始都是辅助代码
    import time
    if one_by_one==1:
        for i in way:
            os.system('clear' if os.name=='posix' else 'cls')
            print_way(mapp,[i])
            time.sleep(0.05)
        time.sleep(2)
        return
    for i in range(len(mapp)):
            for j in range(len(mapp[i])):
                if [i,j] in way:
                    print("*",end=" ")
                else:
                    print(mapp[i][j],end=" ")
            print()
def print_mapp(mapp,width,pots=[]):
    os.system('clear' if os.name=='posix' else 'cls')
    for i in range(len(mapp)):
        print(i+1," "*(len(str(len(mapp)))-len(str(i+1))+1),"|",sep="",end="")
        for j in range(len(mapp[i])):
            if [i,j] in pots:
                print("*",end=" ")
            else:
                print(mapp[i][j],end=" ")
        print()
    print(" "*(len(str(len(mapp)))+1)+"-"*(len(mapp[0])*2))
    for i in range(len(str(len(mapp)))):
        print(" "*(len(str(len(mapp)))+2),end="")
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
mapp=[]
for i in range(lenth):
     mapp+=[input().replace("\xa0"," ")]
print_mapp(mapp,width)

start=[]
end=[]
start.append(int(input("输入起点行数:"))-1)
start.append(int(input("输入起点列数:"))-1)
print_mapp(mapp,width,pots=[start])
print("*是启点的位置")
end.append(int(input("输入终点行数:"))-1)
end.append(int(input("输入终点列数:"))-1)
print_mapp(mapp,width,pots=[end])
print("*是终点的位置")
enter=input("按ENTER键开始寻路")
os.system('clear' if os.name=='posix' else 'cls')
w=find_way(mapp,start,end)
print("找到以下路线")
while 1:
    for i in range(len(w)):
        print("第"+str(i+1)+"条：")
        print_way(mapp,w[i])
        print()
    i=int(input("输入要演示的路线编号："))-1
    print_way(mapp,w[i],one_by_one=1)
    os.system('clear' if os.name=='posix' else 'cls')
