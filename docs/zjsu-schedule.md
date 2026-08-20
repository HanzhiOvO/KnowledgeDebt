# 浙江工商大学本科教务连接器

连接器标识：`zjsu_undergraduate_v9`；界面显示“浙江工商大学本科教务系统 V-9.0”。

## 当前能力边界

当前版本只实现安全的连接器接口、状态机和版本化脱敏 fixture 解析器，没有猜测学校登录或课表 API，也不尝试绕过验证码、SSO 或扫码认证。实时登录显示为“等待验证”。要实现真实连接，需要项目所有者提供以下任一种授权材料：

- 专门测试账号；或
- 从本人会话导出的脱敏 HAR，移除 Cookie、Authorization、账号、学号、手机号和其他个人信息。

在验证前，fixture 导入是唯一启用的同步方式。这是功能边界，不是“已经支持实时教务”的暗示。

## Fixture 格式

示例见 [`fixtures/zjsu-schedule.example.json`](fixtures/zjsu-schedule.example.json)。顶层必须包含：

- `schema`: 固定为 `knowledgedebt.zjsu.schedule.fixture.v1`；
- `term`: 学期名、起止日期和时区；
- `period_times`: 本学期每节课真实起止时刻，系统不会猜当前作息；
- `courses`: 课程、星期、节次、周次、单双周、教师、校区/楼宇/教室；
- `adjustments`: 可选的调课、补课和停课记录。

导入会先生成 Academic Term、Schedule Rule 和 Occurrence。未来 Occurrence 只显示在课表，不创建 Session，也不形成知识债务。只有课堂已发生、用户打开课堂或有证据归档时才幂等物化 Session；停课记录永远不能创建 Session。

## 同步与会话安全

计划同步间隔默认为 360 分钟，可通过 `KNOWLEDGEDEBT_SCHEDULE_SYNC_INTERVAL_MINUTES` 调整。未来启用实时连接后，只允许加密保存 Cookie/Session，不保存账号密码；认证失效必须进入 `reauth_required`，不能高频重试。
