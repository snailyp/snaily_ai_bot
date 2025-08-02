# 从零部署 Gemini Balance 手册

感谢[曾佬](https://linux.do/u/slashkkk)提供的render和clawcloud教程

> [!CAUTION]
>
> #### 内容概述
>
> 1. 为零代码基础用户提供“入门级”路径。
> 2. 极简配置，轻量维护。
> 3. 减少后端配置，尽量前端可视化。

## 部署项目篇

> [!NOTE]
>
> ##### 基本步骤
>
> 1. 注册账号
> 2. 部署项目
> 3. 监控面板配置
> 4. 接入工具

### 项目部署系列之 ClawCloud Run 部署

1. #### 注册账号

   > [!IMPORTANT]
   >
   > 1.免费使用前提条件。需通过 Github 账户登录，且 Github 账户注册时间超过 180 天。可在登录前自行前往github.com 个人资料界面确认。否则，可能导致一个月后本项目无法运行。
   >
   > 2.没有符合条件的 Github 账户怎么办？（1）更换部署方式；（2）购买，价格约 10 元左右/个。

   - 进入 ClawCloud Run 页面：<https://run.claw.cloud>，点击“Get started for free”。

<img src="https://raw.githubusercontent.com/slashkkk/typora/main/20250511115222136.png" alt="image-20250510012805697" style="zoom:50%;" />

   - 点击 Github 登录

![image-20250510013236290](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115231899.png)

   - 进入 Github 登录界面后，输入账户名、密码，随后点击 Sign in。

![image-20250510013643121](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115243830.png)

   2. #### 部署项目

   - 进入主界面后，点击左上角 Region，选择服务器地址。推荐选择 Singapore。选择完成后，网页会刷新，并在服务器地址前☑️。

![image-20250510014049153](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115249925.png)

   - 点击 App Launchpad。

![image-20250510235220258](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115303478.png)

   - 进入页面后，点击页面右上角 Create App，进入配置页面。

![image-20250510014403272](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115312163.png)

   - 进入配置页面后，按顺序填写信息（不熟悉项目名称的，请打开在线翻译）。

     - Application Name：为方便识别管理，建议填写本项目名字```geminibalance```

     - Image： ```Public```

       - Image Name：```ghcr.io/snailyp/gemini-balance:latest```

       ![image-20250510234537611](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115317145.png)

      - Usage：```Fixed```；

      - Replicas：```1```

      - CPU：```1```

      - Memory：```512```

         > 说明：1. 若登录账号只选择部署 1 个项目，则推荐上述最高免费配置。2.目前免费用户每月流量为 10g，超出部分 0.05 美元/g。可根据流量使用情况选择服务器配置。

         ![image-20250510020253079](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115321005.png)

     - Network

       - Container Port：```8000```
       - Enable Internet：点选为```Access```状态

       ![image-20250510020332217](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115325230.png)

     - **Advanced Configura**

       - Environment Variables：点击 Add，随后粘贴以下变量。填写完成后点击 Add 完成配置。

         ```
         API_KEYS=[""]
         ALLOWED_TOKENS=["",""]
         AUTH_TOKEN=
         MYSQL_HOST=
         MYSQL_PORT=
         MYSQL_USER=
         MYSQL_PASSWORD=
         MYSQL_DATABASE=
         TZ=Asia/Shanghai
         ```

         > [!IMPORTANT]
         >
         > **变量说明**
         >
         > | **变量名**       | 说明                                                         | 格式及示例                                          |
         > | ---------------- | ------------------------------------------------------------ | --------------------------------------------------- |
         > | `API_KEYS`       | Gemini API 密钥列表，用于负载均衡                            | `["your-gemini-api-key-1","your-gemini-api-key-2"]` |
         > | `ALLOWED_TOKENS` | 允许访问的 Token 列表                                        | `["your-access-token-1","your-access-token-2"]`     |
         > | `AUTH_TOKEN`     | 【可选】超级管理员token，具有所有权限，不填默认使用 ALLOWED_TOKENS 的第一个 | `sk-123456`                                         |
         >
         > - 5 个"MYSQL"开头的变量为数据库配置，请参照部署文档有关数据库篇目获取。
         >
         > - TZ=Asia/Shanghai 用来控制时区方便看日志信息。
         >
         > - 最简完整示例如下：
         >
         >   ```
         >   API_KEYS=["your-gemini-api-key-1","your-gemini-api-key-2"]
         >   ALLOWED_TOKENS=["your-access-token-1","your-access-token-2"]
         >   MYSQL_HOST=修改
         >   MYSQL_PORT=修改
         >   MYSQL_USER=修改
         >   MYSQL_PASSWORD=修改
         >   MYSQL_DATABASE=修改
         >   TZ=Asia/Shanghai
         >   ```
         >
         >   > 注意：API_KEYS 和 ALLOWED_TOKENS 的值可以先不修改，待进入监控面板后再更正。（若按图中配置，则进入监控页面的密码为your-access-token-1）
         >   >
         >   > 5 个 MYSQL 数据库变量值必须填写，否则导致部署失败。


![image-20250510020415152](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115332470.png)

​

​  ![image-20250511100650322](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115341744.png)

- 返回页面最上方，点击 Deploy Application。弹窗提示Are you sure you want to deploy the application?选择 Yes。

  ![image-20250511120648573](https://raw.githubusercontent.com/slashkkk/typora/main/20250511120653544.png)

- 等待几秒后，跳转至状态界面。此时，请确认页面左上角显示 running。若显示为其他状态，请稍后片刻；仍未显示 running，请确认你配置选项是否正确。需要修改的，可点击页面右下角 Manage Network 选项修改参数，或删除该项目重新部署。

  ![image-20250511120554449](https://raw.githubusercontent.com/slashkkk/typora/main/20250511120600152.png)

- 将页面滚动到最下方，在 Network 选项卡中，查看右侧公网地址配置情况。一般情况下，显示 pending 表明还在处理当中，需要等待 2～5 分钟，直至pending 状态变为Available。 实际情况下， 2 分钟后即使公网地址前显示pending，亦可尝试在新的浏览器标签中打开该地址。若网页能正常显示本项目登录界面，则正常使用即可。若公网地址前的pending状态超过 10 分钟，且无法打开登录界面，原因可能是服务器过载，需要更长等待时间。建议换区或换服务商重新部署。

  ![image-20250511120429596](https://raw.githubusercontent.com/slashkkk/typora/main/20250511120452198.png)

3. #### 监控页面配置

   - 复制项目公网访问地址后，在浏览器中打开。随后进入登录界面，输入密码即可进入。（如果没有更改配置，密码是```your-access-token-1```）

   ![image-20250510023055219](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115409852.png)

   - 修改API 密钥和访问令牌。

   - 完成上述配置后到页面最下方点击“保存配置”，并确认提示“配置保存成功”。

   ![image-20250510224438602](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115417087.png)

   > [!NOTE]
   >
   > 完成以上步骤后，就可以在工具中调用本项目。
   >
   > 更高阶的配置，请参阅项目文档。

4. #### 接入工具

   以 cherry studio 为例

   - 添加提供商。
     - 提供商名称：任意填写
     - ![image-20250511100831232](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115425468.png)
   - 配置
     - API 密钥：填写监控面板“API 密钥”选项卡中的“允许的令牌列表”。
     - API 地址：填写 ClawCloud 中的公网访问地址。注意结尾不要出现“/”。![image-20250510230659798](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115434846.png)

   - 添加模型

     点击“管理”，添加模型。

     ![image-20250510230909905](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115449358.png)

     至此，部署完成。

------

### 项目部署系列之 Render 部署

1. #### 注册账号

   > [!CAUTION]
   >
   > 1. 免费使用前提条件。部分账号需要绑卡才能免费使用。如遇绑卡要求，请更换高质量节点重试。
   >
   > 2. 免费配额。每个月有750小时的免费时长，超过后会自动停机。
   >
   > 3. 休眠问题。对于免费项目，如果超过15分钟没有web请求,，会自动停机休眠。需要采取“保活”措施（详见系列文章有关部分）。对于已经休眠停机的项目，如果有web请求，会自动唤醒，并且开始对外提供服务，直到下次休眠。

   - 进入 Render 注册页面：<https://dashboard.render.com/register>，选择任一方式注册。

   ![image-20250510194701397](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115458052.png)

2. #### 部署项目

   - 点选创建 Web Services 项目。![image-20250510195151785](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115510161.png)

   - 选择项目镜像地址 Source Code。

     - 点选 Public Git Repository，然后在下方填入 Gemini Balance 项目的 Github 仓库地址。```https://github.com/snailyp/gemini-balance```，然后点击再点击 connect。![image-20250510195822141](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115515405.png)
     - 随后相关字段会自动显示为```snailyp/gemini-balance```。
       ![image-20250510200017078](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115815672.png)

   - 填写核实其他配置信息。

     - Name：默认为```gemini-balance```。可不修改。
     - Language：默认为```Docker```。不修改。
     - Region：默认为```Oregon（US West）```。建议修改为```Singapore （Southeast Asia）```。
     - Instance Type：修改为```For hobby projects—Free```。![image-20250510200532975](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115525549.png)

   - 填写变量 Environment Variables。

     - 点选 ```Add Environment Variable```或```Add from .env```添加变量。其中，前者为逐个添加，后者为批量添加。需要填写的变量如下：

       ```
       API_KEYS=[""]
       ALLOWED_TOKENS=["",""]
       AUTH_TOKEN=
       MYSQL_HOST=
       MYSQL_PORT=
       MYSQL_USER=
       MYSQL_PASSWORD=
       MYSQL_DATABASE=
       TZ=Asia/Shanghai
       ```

         > [!IMPORTANT]
         > **变量说明**
         >
         > | **变量名**       | 说明                                                         | 格式及示例                                          |
         > | ---------------- | ------------------------------------------------------------ | --------------------------------------------------- |
         > | `API_KEYS`       | Gemini API 密钥列表，用于负载均衡                            | `["your-gemini-api-key-1","your-gemini-api-key-2"]` |
         > | `ALLOWED_TOKENS` | 允许访问的 Token 列表                                        | `["your-access-token-1","your-access-token-2"]`     |
         > | `AUTH_TOKEN`     | 【可选】超级管理员token，具有所有权限，不填默认使用 ALLOWED_TOKENS 的第一个 | `sk-123456`                                         |
         >
         > - 5 个"MYSQL"开头的变量为数据库配置，请参照部署文档有关数据库篇目获取。
         >
         > - TZ=Asia/Shanghai 用来控制时区方便看日志信息。
         >
         > - 最简完整示例如下：
         >
         >   ```
         >   API_KEYS=["your-gemini-api-key-1","your-gemini-api-key-2"]
         >   ALLOWED_TOKENS=["your-access-token-1","your-access-token-2"]
         >   MYSQL_HOST=修改
         >   MYSQL_PORT=修改
         >   MYSQL_USER=修改
         >   MYSQL_PASSWORD=修改
         >   MYSQL_DATABASE=修改
         >   TZ=Asia/Shanghai
         >   ```
         >
         >   > 注意：API_KEYS 和 ALLOWED_TOKENS 的值可以先不修改，待进入监控面板后再更正。（若按图中配置，则进入监控页面的密码为your-access-token-1）
         >   >
         >   > 而 5 个 MYSQL 数据库变量值必须填写，否则导致部署失败。
         >
         >   ![image-20250510223416736](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115533672.png)

   - 填写完上述信息后，点击左下角```Deploy web service```。此时，会自动进入 doker 部署界面。请等待 3—5 分钟，直至显示 **Your** **service** **is** **live** **🎉**。
     ![image-20250510201835891](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115538986.png)

3. #### **监控面板配置**

   - 进入控制面板编辑管理。
     - 复制项目的公网访问地址，并在浏览器中打开。如按上述配置，则进入界面的密码是```your-access-token-1```。
       - ![image-20250510223552176](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115553998.png)

​  ![image-20250510223722355](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115546261.png)

- 修改API 密钥和访问令牌。

  ![image-20250510224245527](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115558987.png)

- 完成上述配置后到页面最下方点击“保存配置”，并确认提示“配置保存成功”。

![image-20250510224438602](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115607441.png)

> [!NOTE]
>
> 完成以上步骤后，就可以在工具中调用本项目。
>
> 更高阶的配置，请参阅项目文档。

4. #### 接入工具

   以 cherry studio 为例

   - 添加提供商。
     - 提供商名称：任意填写
     - ![image-20250511100846442](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115619176.png)

   - 配置
     - API 密钥：填写监控面板“API 密钥”选项卡中的“允许的令牌列表”。

     - API 地址：填写 Render 中的公网访问地址。注意结尾不要出现“/”。

       ![image-20250511120201024](https://raw.githubusercontent.com/slashkkk/typora/main/20250511120208252.png)

   - 添加模型

     点击“管理”，添加模型。![image-20250510225733274](https://raw.githubusercontent.com/slashkkk/typora/main/20250511115633206.png)

​  至此，部署完成。

---

## 常见问题补充

1. 关于提示密码错误的问题。如果配置数据库之后，无法通过 AUTH_TOKEN 登录界面（提示"密码错误"），可能是因为数据库的问题。排查步骤如下：

- 如果是新部署项目、但使用了以前的数据库，则可尝试通过以前配置过的密码登录。原因是实际的AUTH_TOKEN在项目初始化的时候，就已经保存到数据库中，后面修改环境变量，不会生效，所以建议在项目初始化之前就要设置好AUTH_TOKEN。也可以把这个过程理解为数据库的迁移，所以利用老数据库部署新项目时，实际上只需要配置 5 个 MYSQL 变量即可。
  - 如果要使用新的登录密码，请重新关联新的数据库。
- 如果是新部署项目、并且使用了新的数据库，请再次核对配置变量信息及数据库配置信息。
