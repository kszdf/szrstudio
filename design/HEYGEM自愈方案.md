# HEYGEM 自动复活方案（给老板/运营看的大白话说明书）

> 目标：让「数字人出片服务 HEYGEM」**自己挂了自己爬起来**，不再需要你半夜手动救，客户出片时也不会因为服务挂了而投诉。
> 配套脚本：`heygem_watchdog.py`（看门狗，已随本方案提供）

---

## 一、这套方案做了哪三件事（层层兜底）

| 防线 | 干什么 | 谁来做 | 要你操作吗 |
|---|---|---|---|
| 1. 容器重启策略 | 容器崩溃后 Docker 自动拉起 | Docker 自身 | 设一次（命令见下） |
| 2. 看门狗脚本 | 每分钟查一次，不在就拉起/重启，写日志 | 任务计划程序定时跑 | 装一次（命令见下） |
| 3. 出片前自检 | 平台提交出片前先确认 HEYGEM 活着 | 后续接进 rewrite_studio.py | 后期加（可选） |

---

## 二、一次性安装步骤（照抄即可）

> ⚠️ 以下命令需要**管理员权限**的 PowerShell（右键"PowerShell"→"以管理员身份运行"）。
> 其中第 3 步的 Python 路径，请用你机器上实际的路径；WorkBuddy 自带的在
> `C:\Users\lenovo\.workbuddy\binaries\python\versions\3.13.12\python.exe`

### 第 1 步：让 Docker 崩溃后自动重启容器（设一次，永久有效）
```powershell
docker update --restart unless-stopped heygem-gen-video
```

### 第 2 步：让 Docker Desktop 开机自启 + 自动起容器
- 打开 Docker Desktop → 右上角齿轮（设置）→ **General**
- 勾选 ✅ **Start Docker Desktop when you log in**（登录时启动）
- 勾选 ✅ **Start containers when Docker starts**（Docker 启动时自动起容器）
- 点 Apply & Restart

### 第 3 步：把看门狗装成"每 2 分钟自动跑"的任务
把下面命令里的**两个路径**改成你机器上的实际位置后执行：
```powershell
schtasks /create /tn "HEYGEM_Watchdog" `
  /tr "'C:\Users\lenovo\.workbuddy\binaries\python\versions\3.13.12\python.exe' 'C:\Users\lenovo\WorkBuddy\慧根堂短视频平台开发\heygem_watchdog.py'" `
  /sc minute /mo 2 /ru lenovo
```
- `/mo 2` = 每 2 分钟跑一次（想更灵敏可改 `/mo 1` 每分钟）
- 装好后可在「任务计划程序」里看到 `HEYGEM_Watchdog`，可手动右键"运行"测试

### 第 4 步：验证
1. 手动停掉容器模拟崩溃：`docker stop heygem-gen-video`
2. 等 2 分钟，看 `heygem_watchdog.log` 是否出现 `✅ 已自动启动容器`
3. 浏览器开 `http://localhost:8383` 确认出片服务恢复

---

## 三、日志在哪、怎么看
- 日志文件：`heygem_watchdog.log`（和脚本同目录）
- 正常时每 2 分钟多一行 `✅ HEYGEM 正常`
- 异常时会有 `⚠` / `❌` 提示，方便你判断是"Docker 没开"还是"内存撑爆"

---

## 四、根因提醒：它为什么崩（137 = 内存撑爆）
容器状态显示 `Exited (137)` 几乎都是**内存被吃满被系统强杀**。最常见诱因：
- 同时点了多条出片，内存叠加爆掉（你们现在没有"排队"，会一起挤）。

**减少崩溃的顺手做法**：让出片**一次只跑一条**（加个简单队列）。这能把"经常崩"变成"基本不崩"，看门狗只当最后兜底。

---

## 五、一个要说清楚的限制
HEYGEM 依赖 **Docker Desktop**（你电脑上的用户程序，不是系统后台服务）。
所以"电脑彻底重启后全自动恢复"的前提是：**有人登录了电脑 + Docker Desktop 已设开机自启**（第 2 步）。
- 看门狗解决的是最常见场景：**电脑开着、容器中途崩了**——这种情况下它自己就爬起来了。
- 真要做到"无人登录也 7×24 自动跑"，需把 Docker 换成无桌面的引擎版，属于后期规模化再考虑，现在不必。

---

## 六、后续可加的"平台自检"（让客户零感知）
在 `rewrite_studio.py` 真正提交出片前，加一段：
- 探一下 `http://localhost:8383` 是否通；
- 不通就先 `docker start heygem-gen-video`，等起来再提交；
- 仍不通则返回友好提示"出片服务重启中，请 1 分钟后再试"，而不是静默失败。

这一步我可以在你确认后接进代码（属小改动，按铁律需管理员重启 HGTStudio 才生效）。

---

### 小结
- **现在就能做、且基本 0 成本**：第 1–3 步（设重启策略 + 装看门狗）。
- **效果**：HEYGEM 中途挂了，2 分钟内自动复活，你睡着也能恢复，客户出片不再因服务挂掉而投诉。
- **根上减少崩溃**：出片改成一次一条（排队）。
