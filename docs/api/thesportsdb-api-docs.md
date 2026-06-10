# TheSportsDB API 文档

> 来源：本地离线 HTML `TheSportsDB 免费体育 API 文档 - TheSportsDB.com.html`，保存自 `https://www.thesportsdb.com/documentation#v1`。

## 介绍

欢迎使用 TheSportsDB API，这里是您轻松获取免费实时体育数据的理想之选。无论您是开发应用程序、运营梦幻联赛，还是仅仅想随时了解实时比分、球员数据、球队排名或赛程，我们强大易用的 API 都能满足您的需求。TheSportsDB API 支持全球范围内的众多体育项目和联赛，专为希望集成可靠体育数据而无需支付昂贵订阅费用的开发者、爱好者和初创公司而设计。立即开始，让您的应用程序充满精彩赛事！

## 免费版与付费版

我们提供任何人都可以使用的免费 API。网站最初提供免费 API 的所有方法，没有任何限制，但遗憾的是，它过于受欢迎，导致被滥用。因此，多年来，我们不得不限制某些方法的使用，同时努力保持核心功能。免费 API 应该适用于许多使用场景，所以请您务必测试一下。

当前的免费 API 密钥是：123。

升级后，您可以在用户个人资料中找到高级 API 密钥。

高级 API 包含 V1 版本中的所有方法，并具有更大的限制，以及一些额外功能，例如实时比分和视频集锦链接。高级版还允许您使用更现代的 V2 API。如果您是支持者，可以在用户个人资料中找到您的高级 API 密钥。

[升级至高级版（每月 9 美元）](https://www.thesportsdb.com/pricing)

## API v1 与 v2

v1 API 编写于十多年前，此后不断添加新功能。它使用基本的 PHP 代码生成 JSON 对象并返回给用户。API 用户通过 URL 中的简单数字 API 密钥进行身份验证。命名略显混乱，身份验证机制也不够完善，因为任何想要窥探您 Web 请求的人都可以看到密钥。尽管如此，它仍然可用，并且简单易用，在任何 Web 浏览器中都易于测试。非常适合初学者！

v2 API 则更加现代化，功能也更加完善。这意味着它应该更容易理解，逻辑性更强。它还采用了更现代的身份验证方式，要求在请求头中发送 API 密钥。如果出现问题，API 将返回标准的 HTTP 响应代码。v2 仅供高级订阅用户使用，并且是未来唯一开发的版本。

## 基本 URL

基本 URL 是进行 API 调用时的关键要素，它允许您明确指定 API 请求的根 URL。进行 API 调用时，您需要将基本 URL 与特定的端点路径组合起来，形成完整的请求 URL。

```text
v1 Base URL = https://www.thesportsdb.com/api/v1/json
v2 Base URL = https://www.thesportsdb.com/api/v2/json
```

## V1 认证

API v1 的身份验证过程非常简单。只需使用上面的基本 URL，并在 URL 后附加数字 /123/ 即可获得免费密钥，或者替换为您的高级 API 密钥。

使用免费密钥：

```text
https://www.thesportsdb.com/api/v1/json/123/searchteams.php?t=Arsenal
```

使用您的高级 API 密钥：

```text
https://www.thesportsdb.com/api/v1/json/YOUR_API_KEY_GOES_HERE/searchteams.php?t=Arsenal
```

下面您可以看到一个使用简单的网络浏览器和免费的“123”API密钥的示例：

## V2 认证

v2 版本采用了一种更安全、更现代的身份验证方式。

向 API 基本 URL 发送请求时，必须在请求头中使用属性“X-API-KEY”包含 API 密钥。您可以在下方看到一个使用流行的免费 API 测试软件[Httpie 的](https://httpie.io/)

示例。

## 图片

我们的网站拥有海量图片，涵盖赛事、球员和球队等内容。大部分图片由粉丝创作。

图片分为两种类型：JPEG 格式的粉丝作品和透明 PNG 格式的图片（主要用于徽章和标志）。您可以在此页面

查看不同

[类型的作品和尺寸](https://www.thesportsdb.com/docs_artwork.php)

。
您可以使用从 JSON 数据返回的图片 URL，通过前端访问任何图片。

预览图片：

大多数情况下，您可能并不想下载原始大图，而只想查看小图预览。

只需在 URL 末尾添加“/medium”、“/small”或“/tiny”即可。这样就能获得较小的预览版本。

原图 720px -

[/league/fanart/xpwsrw1421853005.jpg](./TheSportsDB 免费体育 API 文档 - TheSportsDB.com_files/xpwsrw1421853005.jpg)

中等尺寸 500px -

[/league/fanart/xpwsrw1421853005.jpg/medium](./TheSportsDB 免费体育 API 文档 - TheSportsDB.com_files/medium)

小图 250px -

[/league/fanart/xpwsrw1421853005.jpg/small](./TheSportsDB 免费体育 API 文档 - TheSportsDB.com_files/small)

超小图 50px -

[/league/fanart/xpwsrw1421853005.jpg/tiny](./TheSportsDB 免费体育 API 文档 - TheSportsDB.com_files/tiny)

## 速率限制

本网站针对不同级别的用户设置了不同的请求速率限制。如果您超出限制，将会收到“429”HTTP响应头，您需要等待一分钟左右才能再次发起请求。我们实施这些限制不仅是为了区分不同的用户级别，更是为了保持网站整体性能的稳定性。
免费用户每分钟30次请求；高级用户每分钟100次

请求；商务用户每分钟120次请求。

## V1 API 文档

### v1 API 搜索

大多数 API 请求都会以字符串搜索开始。例如，查找球队或球员的详细信息。

所有搜索都会返回实体数据，以及一个 ID 号，该 ID 号可用于后续更快速的查找。

#### 搜索球队

类型：

`{细绳}`

范围：

`t`

免费限额：

`1`

保费限额：

`10`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/searchteams.php?t=Arsenal)

按名称搜索任何运动队。{strTeam}

```text
https://www.thesportsdb.com/api/v1/json/123/searchteams.php?t=Arsenal
```

#### 搜索事件

类型：

`{细绳}`

范围：

`e`

类型：

`{日期}`

范围：

`d`

类型：

`{细绳}`

范围：

`s`

免费限额：

`1`

保费限额：

`10`

示例：包含赛季的

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/searchevents.php?e=Arsenal_vs_Chelsea)

示例：包含日期的

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/searchevents.php?e=Arsenal_vs_Chelsea&s=2016-2017)

示例：包含文件名的

[JSON 数据示例：](https://www.thesportsdb.com/api/v1/json/123/searchevents.php?e=Arsenal_vs_Chelsea&d=2015-04-26)

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/searchevents.php?f=English_Premier_League_2015-04-26_Arsenal_vs_Chelsea)描述：按标题搜索任何体育赛事，并可按赛季、日期或文件名进行额外筛选：{strEvent}可选字符串：{strSeason} {strDate} {strFilename}

```text
https://www.thesportsdb.com/api/v1/json/123/searchevents.php?e=Arsenal_vs_Chelsea
https://www.thesportsdb.com/api/v1/json/123/searchevents.php?e=Arsenal_vs_Chelsea&s=2016-2017
https://www.thesportsdb.com/api/v1/json/123/searchevents.php?e=Arsenal_vs_Chelsea&d=2015-04-26
https://www.thesportsdb.com/api/v1/json/123/searchevents.php?f=English_Premier_League_2015-04-26_Arsenal_vs_Chelsea
```

#### 搜索文件名

类型：

`{细绳}`

范围：

`e`

类型：

`{细绳}`

范围：

`s`

免费限额：

`1`

保费限额：

`10`

示例：包含赛季信息

[的 JSON 数据示例：](https://www.thesportsdb.com/api/v1/json/123/searchfilename.php?e=English_Premier_League_2015-04-26_Arsenal_vs_Chelsea)

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/searchfilename.php?e=English_Premier_League_2015-04-26_Arsenal_vs_Chelsea&s=2016-2017)描述：按文件名搜索任何体育赛事，并可选择添加赛季筛选条件。{strFilename}可选字符串：{strSeason}

```text
https://www.thesportsdb.com/api/v1/json/123/searchfilename.php?e=English_Premier_League_2015-04-26_Arsenal_vs_Chelsea
https://www.thesportsdb.com/api/v1/json/123/searchfilename.php?e=English_Premier_League_2015-04-26_Arsenal_vs_Chelsea&s=2016-2017
```

#### 搜索玩家

类型：

`{细绳}`

范围：

`p`

免费限额：

`1`

保费限额：

`10`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/searchplayers.php?p=Danny_Welbeck)

描述：按运动员的主要姓名或别名搜索任何运动员。{strPlayer}

```text
https://www.thesportsdb.com/api/v1/json/123/searchplayers.php?p=Danny_Welbeck
```

#### 搜索场地

类型：

`{细绳}`

范围：

`v`

免费限额：

`1`

保费限额：

`10`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/searchvenues.php?v=Wembley)

描述：按名称或别名搜索任何场地 {strVenue}

```text
https://www.thesportsdb.com/api/v1/json/123/searchvenues.php?v=Wembley
```

### v1 API 查询

虽然上面的字符串搜索很有用，但实际上使用唯一 ID 来检索数据要快得多，也简单得多。

查找会返回该实体的所有数据，通常只需查看前端网站的 URL 即可找到任何 ID。

例如，英超联赛的 ID 是 4328，一级方程式赛车的 ID 是 4370。

#### 查找联赛

类型：

`{整数}`

范围：

`ID`

免费限额：

`1`

保费限额：

`1`

示例：使用联赛 ID {idLeague} 查找联赛详细信息

[的 JSON 数据](https://www.thesportsdb.com/api/v1/json/123/lookupleague.php?id=4328)

```text
https://www.thesportsdb.com/api/v1/json/123/lookupleague.php?id=4328
```

#### 查询联赛排名

类型：

`{整数}`

范围：

`ID`

免费限额：

`5`

保费限额：

`100`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/lookuptable.php?l=4328&s=2020-2021)

查找当前联赛积分表，使用其 ID：{idLeague} 可选：{strSeason}（*仅限精选足球联赛）

```text
https://www.thesportsdb.com/api/v1/json/123/lookuptable.php?l=4328
https://www.thesportsdb.com/api/v1/json/123/lookuptable.php?l=4328&s=2020-2021
```

#### 查找团队

类型：

`{整数}`

范围：

`ID`

免费限额：

`1`

保费限额：

`1`

示例：使用团队 ID 查找团队详细信息

[的 JSON 数据。{idTeam}](https://www.thesportsdb.com/api/v1/json/123/lookupteam.php?id=133604)

```text
https://www.thesportsdb.com/api/v1/json/123/lookupteam.php?id=133604
```

#### 查找团队装备

类型：

`{整数}`

范围：

`ID`

免费限额：

`2`

保费限额：

`100`

示例：使用团队 ID 查找团队的历史和当前设备

[信息。{idTeam}](https://www.thesportsdb.com/api/v1/json/123/lookupequipment.php?id=133597)

```text
https://www.thesportsdb.com/api/v1/json/123/lookupequipment.php?id=133597
```

#### 查找球员

类型：

`{整数}`

范围：

`ID`

免费限额：

`1`

保费限额：

`1`

示例：使用玩家 ID 查找玩家详细信息的

[JSON 数据。{idPlayer}](https://www.thesportsdb.com/api/v1/json/123/lookupplayer.php?id=34145937)

```text
https://www.thesportsdb.com/api/v1/json/123/lookupplayer.php?id=34145937
```

#### 查找球员荣誉

类型：

`{整数}`

范围：

`ID`

免费限额：

`5`

保费限额：

`500`

示例：使用

[玩家](https://www.thesportsdb.com/api/v1/json/123/lookuphonours.php?id=34147178)

ID 查询该玩家的所有荣誉。{idPlayer}

```text
https://www.thesportsdb.com/api/v1/json/123/lookuphonours.php?id=34147178
```

#### 查找球员曾效力球队

类型：

`{整数}`

范围：

`ID`

免费限额：

`5`

保费限额：

`100`

示例：使用

[球员](https://www.thesportsdb.com/api/v1/json/123/lookupformerteams.php?id=34147178)

ID 查找该球员的所有曾效力过的球队。{idPlayer}

```text
https://www.thesportsdb.com/api/v1/json/123/lookupformerteams.php?id=34147178
```

#### 查找球员里程碑

类型：

`{整数}`

范围：

`ID`

免费限额：

`5`

保费限额：

`100`

示例：使用玩家 ID 查找该玩家的所有里程碑

[JSON 数据。{idPlayer}](https://www.thesportsdb.com/api/v1/json/123/lookupmilestones.php?id=34161397)

```text
https://www.thesportsdb.com/api/v1/json/123/lookupmilestones.php?id=34161397
```

#### 查找球员合同

类型：

`{整数}`

范围：

`ID`

免费限额：

`1`

保费限额：

`100`

示例：使用

[玩家](https://www.thesportsdb.com/api/v1/json/123/lookupcontracts.php?id=34147178)

ID 查找该玩家的所有合同。{idPlayer}

```text
https://www.thesportsdb.com/api/v1/json/123/lookupcontracts.php?id=34147178
```

#### 查找球员结果

类型：

`{整数}`

范围：

`ID`

免费限额：

`5`

保费限额：

`500`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/playerresults.php?id=34160573)

查找，使用玩家 ID 查找所有结果。{idPlayer}

```text
https://www.thesportsdb.com/api/v1/json/123/playerresults.php?id=34160573
```

#### 查找事件

类型：

`{整数}`

范围：

`ID`

免费限额：

`1`

保费限额：

`1`

示例：使用 ID 查找团队详细信息

[的 JSON 数据。{idEvent}](https://www.thesportsdb.com/api/v1/json/123/lookupevent.php?id=441613)

```text
https://www.thesportsdb.com/api/v1/json/123/lookupevent.php?id=441613
```

#### 查找事件结果

类型：

`{整数}`

范围：

`ID`

免费限额：

`5`

保费限额：

`100`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventresults.php?id=652890)

查找，使用事件 ID 查找所有结果。{idEvent}

```text
https://www.thesportsdb.com/api/v1/json/123/eventresults.php?id=652890
```

#### 查找活动阵容

类型：

`{整数}`

范围：

`ID`

免费限额：

`5`

保费限额：

`100`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/lookuplineup.php?id=1032723)

查找，使用赛事 ID 获取赛事队伍阵容。{idEvent}

```text
https://www.thesportsdb.com/api/v1/json/123/lookuplineup.php?id=1032723
```

#### 查找事件时间线

类型：

`{整数}`

范围：

`ID`

免费限额：

`5`

保费限额：

`100`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/lookuptimeline.php?id=1032718)

查找，使用事件 ID 获取事件的时间线。{idEvent}

```text
https://www.thesportsdb.com/api/v1/json/123/lookuptimeline.php?id=1032718
```

#### 查找事件统计

类型：

`{整数}`

范围：

`ID`

免费限额：

`5`

保费限额：

`100`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/lookupeventstats.php?id=1032723)

查找，使用事件 ID 获取事件统计信息。{idEvent}

```text
https://www.thesportsdb.com/api/v1/json/123/lookupeventstats.php?id=1032723
```

#### 查找活动电视广播

类型：

`{整数}`

范围：

`ID`

免费限额：

`2`

保费限额：

`100`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/lookuptv.php?id=584911)

查找所有播放特定事件的电视频道，使用事件 ID。{idEvent}

```text
https://www.thesportsdb.com/api/v1/json/123/lookuptv.php?id=584911
```

#### 查找场地

类型：

`{整数}`

范围：

`ID`

免费限额：

`1`

保费限额：

`1`

示例：使用团队 ID 查找

[JSON 数据。{idTeam}](https://www.thesportsdb.com/api/v1/json/123/lookupvenue.php?id=16163)

```text
https://www.thesportsdb.com/api/v1/json/123/lookupvenue.php?id=16163
```

### v1 API 列表

使用列表 API 可以返回多条记录。

例如，这对于列出体育项目或国家/地区非常有用。

#### 所有体育项目

类型：

`无效的`

范围：

`没有任何`

免费限额：

`2`

保费限额：

`50`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/all_sports.php)

列表，列出网站上支持的所有体育类别。

```text
https://www.thesportsdb.com/api/v1/json/123/all_sports.php
```

#### 所有国家/地区

类型：

`无效的`

范围：

`没有任何`

免费限额：

`50`

保费限额：

`500`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/all_countries.php)

列表，列出网站支持的所有地理国家/地区。

```text
https://www.thesportsdb.com/api/v1/json/123/all_countries.php
```

#### 所有联赛

类型：

`{无效的}`

范围：

`没有任何`

免费限额：

`10`

保费限额：

`3000`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/all_leagues.php)

列表 TheSportsDB 上的所有联赛。

```text
https://www.thesportsdb.com/api/v1/json/123/all_leagues.php
```

#### 联赛列表

类型：

`{细绳}`

范围：

`c`

国家名称

类型：

`{细绳}`

范围：

`s`

运动名称

：自由限制

`10`

限额：高级限额：

`100`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/search_all_leagues.php?c=England&s=Soccer)

列表，列出某个国家/地区特定运动项目的所有联赛。{strCountry} {strSport}

```text
https://www.thesportsdb.com/api/v1/json/123/search_all_leagues.php?c=England&s=Soccer
```

#### 列出赛季

类型：

`整数`

范围：

`ID`

免费限额：

`5`

保费限额：

`500`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/search_all_seasons.php?id=4328)

示例2：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/search_all_seasons.php?id=4328&poster=1)

示例3：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/search_all_seasons.php?id=4328&badge=1)

示例4：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/search_all_seasons.php?id=4328&description=1)

列出指定联赛 ID 的所有可用赛季。{idLeague}

```text
https://www.thesportsdb.com/api/v1/json/123/search_all_seasons.php?id=4328
https://www.thesportsdb.com/api/v1/json/123/search_all_seasons.php?id=4328&poster=1
https://www.thesportsdb.com/api/v1/json/123/search_all_seasons.php?id=4328&badge=1
https://www.thesportsdb.com/api/v1/json/123/search_all_seasons.php?id=4328&description=1
```

#### 列出球队

类型：

`细绳`

范围：

`我`

类型：

`细绳`

范围：

`s`

类型：

`细绳`

范围：

`c`

免费限额：

`10`

保费限额：

`3000`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/search_all_teams.php?l=English_Premier_League)

示例2：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/search_all_teams.php?s=Soccer&c=Spain)

按联赛名称、国家/地区和运动项目或 ID 列出特定联赛中的所有球队。{strLeague} {strCountry} {strSport}

```text
https://www.thesportsdb.com/api/v1/json/123/search_all_teams.php?l=English_Premier_League
https://www.thesportsdb.com/api/v1/json/123/search_all_teams.php?s=Soccer&c=Spain
```

#### 列出玩家

类型：

`整数`

范围：

`ID`

免费限额：

`10`

保费限额：

`3000`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/lookup_all_players.php?id=133604)

列表，列出所有为某个球队效力的球员，并按球队 ID 排序。{idTeam}

```text
https://www.thesportsdb.com/api/v1/json/123/lookup_all_players.php?id=133604
```

### v1 API 计划

赛程 API 允许您查找过去、现在和未来的赛事。

它支持查找整个赛季，以及查找部分即将到来的或过去的赛事。

#### 球队下一场比赛

类型：

`整数`

范围：

`ID`

免费限额：

`1`

保费限额：

`10`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventsnext.php?id=133602)

使用团队 ID 查看团队接下来即将举行的几项赛事。*免费密钥仅显示主赛事

```text
https://www.thesportsdb.com/api/v1/json/123/eventsnext.php?id=133602
```

#### 日程安排团队上一页

类型：

`整数`

范围：

`ID`

免费限额：

`1`

保费限额：

`10`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventslast.php?id=133602)

使用团队 ID 查看团队最近的几项赛事。*免费密钥仅显示主场赛事

```text
https://www.thesportsdb.com/api/v1/json/123/eventslast.php?id=133602
```

#### 联赛下一赛程

类型：

`整数`

范围：

`ID`

免费限额：

`1`

保费限额：

`20`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventsnextleague.php?id=4328)

使用联赛 ID 查看球队即将举行的赛事。

```text
https://www.thesportsdb.com/api/v1/json/123/eventsnextleague.php?id=4328
```

#### 赛程联赛上期

类型：

`整数`

范围：

`ID`

免费限额：

`1`

保费限额：

`20`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventspastleague.php?id=4328)

使用联赛 ID 查看球队即将举行的赛事。{idLeague}

```text
https://www.thesportsdb.com/api/v1/json/123/eventspastleague.php?id=4328
```

#### 日程安排日期

类型：

`日期`

范围：

`d`

类型：

`细绳`

参数（可选）：

`s`

类型：

`细绳`

参数（可选）：

`我`

免费限额：

`3`

保费限额：

`1500`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventsday.php?d=2014-10-10)

示例2：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventsday.php?d=2014-10-10&s=Baseball)

示例3：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventsday.php?d=2014-10-10&l=MLB)

示例4：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventsday.php?d=2014-10-10&l=4424)

查看未来、过去或当前特定日期的赛事。

您还可以添加联赛 ID 或名称的筛选条件（可选）。{dateEvent} {strSport} {idLeague}

```text
https://www.thesportsdb.com/api/v1/json/123/eventsday.php?d=2014-10-10
https://www.thesportsdb.com/api/v1/json/123/eventsday.php?d=2014-10-10&s=Baseball
https://www.thesportsdb.com/api/v1/json/123/eventsday.php?d=2014-10-10&l=4424
```

#### 赛季安排

类型：

`整数`

范围：

`ID`

类型：

`细绳`

范围：

`s`

免费限额：

`15`

保费限额：

`3000`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventsseason.php?id=4328&s=2014-2015)

查看特定赛季的所有赛事，并按联赛 ID 筛选。{idSeason} {strSeason}

```text
https://www.thesportsdb.com/api/v1/json/123/eventsseason.php?id=4328&s=2014-2015
```

#### 电视节目表

类型：

`整数`

范围：

`d`

类型：

`日期`

范围：

`s`

类型：

`细绳`

范围：

`一个`

类型：

`细绳`

范围：

`c`

类型：

`细绳`

范围：

`ID`

类型：

`整数`

免费限额：

`1`

保费限额：

`500`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventstv.php?d=2024-07-07)

示例2：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventstv.php?d=2018-07-07&s=Fighting)

示例3：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventstv.php?d=2019-09-28&a=United_Kingdom&s=Cycling)

示例4：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventstv.php?c=Peacock_Premium)

示例5：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventstv.php?id=7000)

查看特定日期的电视节目表。{dateEvent} {strSport} {strCountry} {strChannel} {idChannel}

```text
https://www.thesportsdb.com/api/v1/json/123/eventstv.php?d=2024-07-07
https://www.thesportsdb.com/api/v1/json/123/eventstv.php?d=2018-07-07&s=Fighting
https://www.thesportsdb.com/api/v1/json/123/eventstv.php?d=2019-09-28&a=United_Kingdom&s=Cycling
https://www.thesportsdb.com/api/v1/json/123/eventstv.php?c=Peacock_Premium
https://www.thesportsdb.com/api/v1/json/123/eventstv.php?id=7000
```

### v1 API 视频

视频 API 允许您列出与某个赛事相关的所有 YouTube 精彩集锦。

请注意，我们无法控制 YouTube，部分视频可能仅限特定国家/地区观看。

您可以使用各种筛选条件，包括单独按日期筛选，或结合联赛 ID 或体育项目筛选。YouTube精彩集锦视频：类型：

`日期`

范围：

`d`

类型：

`整数`

参数（可选）：

`我`

{联赛 ID}

类型：

`细绳`

参数（可选）：

`s`

{运动名称}

免费限额：

`2`

保费限额：

`50`

示例：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventshighlights.php?d=2024-07-07)

示例 2：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventshighlights.php?d=2024-07-07&l=4684)

示例 3：

[JSON 数据](https://www.thesportsdb.com/api/v1/json/123/eventshighlights.php?d=2024-07-07&s=motorsport)

查看特定日期的电视节目表

```text
https://www.thesportsdb.com/api/v1/json/123/eventshighlights.php?d=2024-07-07
https://www.thesportsdb.com/api/v1/json/123/eventshighlights.php?d=2024-07-07&l=4684
https://www.thesportsdb.com/api/v1/json/123/eventshighlights.php?d=2024-07-07&s=motorsport
```

## V2 API 文档

### v2 API 搜索

#### 搜索联赛

类型：

`{细绳}`

限制：

`10`

示例：使用文本字符串对任何体育联盟进行

[静态](https://www.thesportsdb.com/api/v2/examples/search_league.json)

/

[实时搜索。](https://www.thesportsdb.com/api/v2/json/search/league/english_premier_league)

```text
/api/v2/json/search/league/english_premier_league
```

#### 搜索团队

类型：

`{细绳}`

限制：

`10`

示例：使用文本字符串对任何运动队进行

[静态](https://www.thesportsdb.com/api/v2/examples/search_team.json)

/

[实时搜索。](https://www.thesportsdb.com/api/v2/json/search/league/english_premier_league)

```text
/api/v2/json/search/team/manchester_united
```

#### 搜索玩家

类型：

`{细绳}`

限制：

`10`

示例：使用文本字符串对任何运动员进行

[静态](https://www.thesportsdb.com/api/v2/examples/search_player.json)

/

[实时搜索。](https://www.thesportsdb.com/api/v2/json/search/player/harry_kane)

```text
/api/v2/json/search/player/harry_kane
```

#### 搜索事件

类型：

`{细绳}`

限制：

`10`

示例：使用文本字符串对任何体育赛事进行

[静态](https://www.thesportsdb.com/api/v2/examples/search_event.json)

/

[实时搜索。](https://www.thesportsdb.com/api/v2/json/search/event/fifa_world_cup_2022-12-18_argentina_vs_france)

```text
/api/v2/json/search/event/fifa_world_cup_2022-12-18_argentina_vs_france
```

#### 搜索场地

类型：

`{细绳}`

限制：

`10`

示例：使用文本字符串对任何体育场馆进行

[静态](https://www.thesportsdb.com/api/v2/examples/search_venue.json)

/

[实时搜索。](https://www.thesportsdb.com/api/v2/json/search/venue/wembley)

```text
/api/v2/json/search/venue/wembley
```

### v2 API 查询

#### 查找联赛

类型：

`{整数}`

限制：

`1`

示例：使用唯一 ID {idLeague} 查找任何联赛的

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_league.json)

/

[实时信息。](https://www.thesportsdb.com/api/v2/json/lookup/league/4328)

```text
/api/v2/json/lookup/league/4328
```

#### 查找团队

类型：

`{整数}`

限制：

`1`

示例：使用唯一 ID {idTeam} 查找任何团队的

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_team.json)

/

[实时信息。](https://www.thesportsdb.com/api/v2/json/lookup/team/133597)

```text
/api/v2/json/lookup/team/133597
```

#### 查找团队装备

类型：

`{整数}`

限制：

`1`

示例：使用唯一 ID {idTeam} 查找任何团队装备图案的

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_team_equipment.json)

/

[实时图像。](https://www.thesportsdb.com/api/v2/json/lookup/team_equipment/133597)

```text
/api/v2/json/lookup/team_equipment/133597
```

#### 查找球员

类型：

`{整数}`

限制：

`1`

示例：使用玩家的唯一 ID {idPlayer} 进行

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_player.json)

/

[实时查找。](https://www.thesportsdb.com/api/v2/json/lookup/player/34172575)

```text
/api/v2/json/lookup/player/34172575
```

#### 查找球员合同

类型：

`{整数}`

限制：

`1`

示例：使用球员的唯一 ID {idPlayer} 进行

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_player_contracts.json)

/

[实时球员合同查询。](https://www.thesportsdb.com/api/v2/json/lookup/player_contracts/34146304)

```text
/api/v2/json/lookup/player_contracts/34146304
```

#### 查找球员结果

类型：

`{整数}`

限制：

`1`

示例：使用玩家的唯一 ID {idPlayer}

[进行静态](https://www.thesportsdb.com/api/v2/examples/lookup_player_results.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/lookup/player_results/34160573)

查找任何玩家的结果。

```text
/api/v2/json/lookup/player_results/34160573
```

#### 查找球员荣誉

类型：

`{整数}`

限制：

`1`

示例：使用玩家的唯一 ID {idPlayer}

[进行静态](https://www.thesportsdb.com/api/v2/examples/lookup_player_honours.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/lookup/player_honours/34146304)

查找任何玩家的荣誉。

```text
/api/v2/json/lookup/player_honours/34146304
```

#### 查找球员里程碑

类型：

`{整数}`

限制：

`1`

示例：使用玩家的唯一 ID {idPlayer} 查找任何玩家里程碑的

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_player_milestones.json)

/

[实时数据。](https://www.thesportsdb.com/api/v2/json/lookup/player_milestones/34146304)

```text
/api/v2/json/lookup/player_milestones/34146304
```

#### 查找球员曾效力球队

类型：

`{整数}`

限制：

`1`

示例：使用球员的唯一 ID {idPlayer}

[进行静态](https://www.thesportsdb.com/api/v2/examples/lookup_player_former_teams.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/lookup/player_teams/34146304)

查找，查找任何球员的前球队。

```text
/api/v2/json/lookup/player_teams/34146304
```

#### 查找事件

类型：

`{整数}`

限制：

`1`

例如：使用其唯一 ID {idLeague} 进行任何体育赛事的

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_event.json)

/

[实时查找。](https://www.thesportsdb.com/api/v2/json/lookup/event/441613)

```text
/api/v2/json/lookup/event/441613
```

#### 查找活动阵容

类型：

`{整数}`

限制：

`1`

示例：使用唯一 ID {idEvent} 查找任何体育赛事球员阵容的

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_event_lineup.json)

/

[实时信息。](https://www.thesportsdb.com/api/v2/json/lookup/event_lineup/1937584)

```text
/api/v2/json/lookup/event_lineup/1937584
```

#### 查找事件结果

类型：

`{整数}`

限制：

`1`

示例：使用唯一 ID {idEvent} 查找任何体育赛事球员结果的

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_event_results.json)

/

[实时数据。](https://www.thesportsdb.com/api/v2/json/lookup/event_results/652890)

```text
/api/v2/json/lookup/event_results/652890
```

#### 查找事件统计

类型：

`{整数}`

限制：

`1`

示例：使用唯一 ID {idEvent} 查找任何体育赛事统计数据的

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_event_statistics.json)

/

[实时信息。](https://www.thesportsdb.com/api/v2/json/lookup/event_stats/1937584)

```text
/api/v2/json/lookup/event_stats/1937584
```

#### 查找事件时间线

类型：

`{整数}`

限制：

`1`

示例：使用唯一 ID {idEvent} 查找任何体育赛事时间线的

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_event_timeline.json)

/

[实时信息。](https://www.thesportsdb.com/api/v2/json/lookup/event_timeline/1937584)

```text
/api/v2/json/lookup/event_timeline/1937584
```

#### 查找活动电视节目表

类型：

`{整数}`

限制：

`1`

示例：使用唯一 ID {idEvent} 查找任何体育赛事电视节目表的

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_event_tv_schedule.json)

/

[实时信息。](https://www.thesportsdb.com/api/v2/json/lookup/event_tv/584911)

```text
/api/v2/json/lookup/event_tv/584911
```

#### 查找活动 YouTube 精彩片段

类型：

`{整数}`

限制：

`1`

例如：使用唯一 ID {idEvent} 在 YouTube 上查找任何体育赛事的

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_event_youtube_highlights.json)

/

[实时精彩集锦。](https://www.thesportsdb.com/api/v2/json/lookup/event_highlights/2044892)

```text
/api/v2/json/lookup/event_highlights/2044892
```

#### 查找场地

类型：

`{整数}`

限制：

`1`

示例：使用唯一 ID {idVenue} 对任何体育场馆进行

[静态](https://www.thesportsdb.com/api/v2/examples/lookup_venue.json)

/

[实时查找。](https://www.thesportsdb.com/api/v2/json/lookup/venue/15910)

```text
/api/v2/json/lookup/venue/15910
```

### v2 API 列表

#### 列出联赛球队

类型：

`{整数}`

限制：

`100`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/list_league_teams.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/list/teams/4328)

列出特定联赛中的所有球队，并按其唯一 ID {idLeague} 进行排序。

```text
/api/v2/json/list/teams/4328
```

#### 联赛赛季

类型：

`{整数}`

限制：

`100`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/list_league_seasons.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/list/seasons/4328)

列表，列出联赛使用其唯一 ID {idLeague} 的所有赛季。

```text
/api/v2/json/list/seasons/4328
```

#### 列出团队成员

类型：

`{整数}`

限制：

`100`

示例：使用球队的唯一 ID {idTeam} 查找球队的所有球员的

[静态](https://www.thesportsdb.com/api/v2/examples/list_team_players.json)

/

[实时信息。](https://www.thesportsdb.com/api/v2/json/list/players/133604)

```text
/api/v2/json/list/players/133604
```

### v2 API 过滤器

按日期类型筛选电视节目：

`{日期}`

限制：

`100`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/filter_tv_events_by_date.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/filter/tv/day/2024-06-22)

筛选特定日期 {dateEvent} 的所有电视活动。

```text
/api/v2/json/filter/tv/day/2024-06-22
```

按国家/地区类型筛选电视节目：

`{细绳}`

限制：

`100`

示例：按频道国家/地区 {strCountry} 筛选

[静态](https://www.thesportsdb.com/api/v2/examples/filter_tv_events_by_country.json)

/

[实时电视事件。](https://www.thesportsdb.com/api/v2/json/filter/tv/day/2024-06-22)

```text
/api/v2/json/filter/tv/country/united_kingdom
```

按体育类型筛选电视节目：

`{细绳}`

限制：

`100`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/filter_tv_events_by_sport.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/filter/tv/sport/motorsport)

筛选即将举行的体育赛事 {strSport}。

```text
/api/v2/json/filter/tv/sport/motorsport
```

按频道类型筛选电视节目：

`{细绳}`

限制：

`100`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/filter_tv_events_by_channel.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/filter/tv/channel/sky_sports_main_event)

筛选即将播出的电视节目，按频道名称 {strChannel} 筛选。

```text
/api/v2/json/filter/tv/channel/sky_sports_main_event
```

按频道 ID类型筛选电视节目：

`{整数}`

限制：

`100`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/filter_tv_events_by_channel.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/filter/tv/channel/sky_sports_main_event)

筛选即将播出的电视节目，按频道 ID {idChannel} 筛选。

```text
/api/v2/json/filter/tv/channelid/3834
```

### v2 API 所有

#### 所有国家/地区

类型：

`{无效的}`

限制：

`500`

例如：

[静态](https://www.thesportsdb.com/api/v2/examples/all_countries.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/all/countries)

显示所有支持的国家/地区。

```text
/api/v2/json/all/countries
```

#### 所有运动

类型：

`{无效的}`

限制：

`500`

例如：

[静态](https://www.thesportsdb.com/api/v2/examples/all_sports.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/all/sports)

展示所有支持的体育项目。

```text
/api/v2/json/all/sports
```

#### 所有联赛

类型：

`{无效的}`

限制：

`3000`

例如：

[静态](https://www.thesportsdb.com/api/v2/examples/all_leagues.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/all/leagues)

显示所有支持的联赛。

```text
/api/v2/json/all/leagues
```

### v2 API 计划

#### 联赛

类型接下来的 10 场赛事：

`{整数}`

限制：

`10`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/next_10_events_in_league.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/schedule/next/league/4328)

列出联赛中接下来的 5 场赛事，使用其唯一 ID {idLeague}

```text
/api/v2/json/schedule/next/league/4328
```

#### 联赛

类型前 10 场赛事：

`{整数}`

限制：

`10`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/previous_10_events_in_league.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/schedule/previous/league/4328)

列表，使用联赛的唯一 ID {idLeague} 列出联赛中的前 5 场赛事。

```text
/api/v2/json/schedule/previous/league/4328
```

#### 接下来10个团队

类型赛事：

`{整数}`

限制：

`10`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/next_10_events_in_team.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/schedule/next/team/133612)

列出使用团队唯一 ID {idTeam} 的团队接下来的 5 个事件

```text
/api/v2/json/schedule/next/team/133612
```

#### 团队

类型前 10 项赛事：

`{整数}`

限制：

`10`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/previous_10_events_in_team.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/schedule/previous/team/133612)

列出使用团队唯一 ID {idTeam} 的团队最近 5 场赛事

```text
/api/v2/json/schedule/previous/team/133612
```

#### 接下来 10 个场地

类型的活动：

`{整数}`

限制：

`10`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/next_10_events_in_venue.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/schedule/next/venue/24413)

列出场地接下来的 5 个活动，使用其唯一 ID {idVenue}

```text
/api/v2/json/schedule/next/venue/24413
```

#### 此前 10 场馆

类型活动：

`{整数}`

限制：

`10`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/previous_10_events_in_venue.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/schedule/previous/venue/24413)

列表显示场地最近 5 场活动，使用其唯一 ID {idVenue}

```text
/api/v2/json/schedule/previous/venue/24413
```

#### 完整球队赛季赛程表

类型：

`{整数}`

限制：

`250`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/full_team_season_schedule.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/schedule/full/team/133612)

列出使用球队唯一 ID {idTeam} 的完整赛季赛程

```text
/api/v2/json/schedule/full/team/133612
```

#### 完整联赛赛季赛程表

类型：

`{细绳}`

限制：

`3000`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/full_league_season_schedule.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/schedule/league/4328/2023-2024)

列出联赛的完整赛季赛程，使用其唯一 ID {idLeague}

```text
/api/v2/json/schedule/league/4328/2023-2024
```

### v2 API 实时比分

#### 实时比分 体育

类型：

`{细绳}`

限制：

`100`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/livescore_sport.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/livescore/soccer)

显示特定体育项目的当前比分 {strSport}

```text
/api/v2/json/livescore/soccer
```

#### 实时比分联赛

类型：

`{整数}`

限制：

`100`

示例：

[静态](https://www.thesportsdb.com/api/v2/examples/livescore_league.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/livescore/4399)

显示特定联赛的当前比分，该联赛拥有唯一的 ID {idLeague}

```text
/api/v2/json/livescore/4399
```

#### 所有

类型的实时比分：

`{无效的}`

限制：

`500`

例如：

[静态](https://www.thesportsdb.com/api/v2/examples/livescore_all.json)

/

[实时](https://www.thesportsdb.com/api/v2/json/livescore/all)

显示所有体育项目的当前比分。

```text
/api/v2/json/livescore/all
```

## Readme.io 文档

简介：

Readme.io 是一个很棒的资源，不仅可以查看文档（与此处镜像），还可以测试 API 并查看多种不同语言的示例代码。其沙箱功能带有实时日志和数据，对新手开发者尤其有用。

[v1 Readme.io 文档](https://thedatadb.readme.io/reference/getteambyname#/)

[v2 Readme.io 文档](https://thedatadb.readme.io/reference/searchleaguebyname#/)

## OpenAPI / Swagger

简介：

OpenAPI规范（OAS）定义了一个标准的、与语言无关的HTTP API接口，使人和计算机无需访问源代码、文档或通过网络流量检查即可发现和理解服务的功能。如果定义得当，使用者只需极少的实现逻辑即可理解远程服务并与之交互。OpenAPI

描述随后可供文档生成工具用于展示API，代码生成工具用于生成各种编程语言的服务器和客户端代码，测试工具以及许多其他用例使用。

[v1 OpenAPI 规范](https://www.thesportsdb.com/api/spec/v1/openapi.yaml)

[v2 OpenAPI 规范](https://www.thesportsdb.com/api/spec/v2/openapi.yaml)

## 邮差收藏

简介：

Postman 是一个集设计、构建和扩展 API 于一体的平台。加入超过 4000 万用户的行列，他们已在一个强大的平台上整合了工作流程并提升了 API 管理水平。

[v1 邮差合集](https://www.postman.com/thedatadb/thesportsdb/collection/0t5rbv8/thesportsdb-v1-api)

[v2 邮差合集](https://www.postman.com/thedatadb/thesportsdb/collection/d7hdb1o/thesportsdb-v2-api)

## 人工智能 MCP

简介：

MCP（模型上下文协议）是一个开放协议，它规范了应用程序如何向逻辑逻辑模型 (LLM) 提供上下文信息。您可以将 MCP 理解为人工智能应用的 USB-C 接口。正如 USB-C 提供了一种标准化的方式将设备连接到各种外围设备和配件一样，MCP 也提供了一种标准化的方式将人工智能模型连接到不同的数据源和工具。

[v1 MCP 规范](https://www.thesportsdb.com/api/spec/v1/MCP/index.js)

[v2 MCP 规范](https://www.thesportsdb.com/api/spec/v2/MCP/index.js)
