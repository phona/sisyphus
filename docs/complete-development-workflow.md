# 完整研发流程：从版本规划到接口管理

## 全景流程图

```mermaid
flowchart TB
    subgraph 规划层["📋 1. 版本规划层"]
        PM1[产品经理] --> |输出| PRD[PRD 需求文档]
        PM1 --> |制定| Roadmap[版本路线图]
        Roadmap --> Sprint[迭代计划]
        PRD --> Sprint
    end

    subgraph 评审层["👥 2. 需求评审层"]
        Sprint --> Review[需求评审会]
        Review --> |参与| Dev[开发团队]
        Review --> |参与| QA[测试团队]
        Review --> |参与| UI[设计团队]
        Review --> |输出| Confirmed[确认需求范围]
    end

    subgraph 拆分层["🔨 3. 任务拆分层"]
        Confirmed --> Split[任务拆分]
        Split --> |前端任务| FE_Task[前端开发任务]
        Split --> |后端任务| BE_Task[后端开发任务]
        Split --> |接口任务| API_Task[接口设计任务]
        
        FE_Task --> Jira1[Jira/飞书任务]
        BE_Task --> Jira2[Jira/飞书任务]
        API_Task --> Jira3[Jira/飞书任务 - 阻塞前后端]
    end

    subgraph 接口设计层["🔌 4. 接口设计层 (Apifox 核心)"]
        Jira3 --> APIDesign[接口设计阶段]
        
        APIDesign --> |步骤1| Define[定义接口路径+方法]
        Define --> |步骤2| Schema[定义请求/响应 Schema]
        Schema --> |步骤3| Example[编写示例数据]
        Example --> |步骤4| MockRule[配置 Mock 规则]
        
        MockRule --> Apifox[Apifox 平台]
        Apifox --> |生成| MockURL[Mock 服务 URL]
        Apifox --> |生成| APIDoc[接口文档]
        
        APIDoc --> |评审| APIReview[接口评审会]
        APIReview --> |通过| APIFinal[接口定版]
        APIReview --> |不通过| Schema
        
        APIFinal --> |状态更新| Jira3
        MockURL --> |提供给| FE_Task
    end

    subgraph 开发层["💻 5. 并行开发层"]
        direction TB
        
        subgraph 前端开发["前端开发"]
            FE_Start[启动开发] --> FE_Config[配置 Mock URL]
            FE_Config --> FE_Dev[按接口文档开发]
            FE_Dev --> FE_Mock[对接 Mock 数据]
            FE_Mock --> FE_UI[开发 UI 逻辑]
            FE_UI --> FE_Test[编写契约测试]
        end
        
        subgraph 后端开发["后端开发"]
            BE_Start[启动开发] --> BE_Impl[实现接口逻辑]
            BE_Impl --> BE_Test[单元测试]
            BE_Test --> BE_Doc[更新 Apifox 实现状态]
        end
        
        APIFinal --> FE_Start
        APIFinal --> BE_Start
    end

    subgraph 联调层["🔗 6. 联调验收层"]
        FE_Test --> |Ready| Integration[联调阶段]
        BE_Doc --> |Ready| Integration
        
        Integration --> SwitchEnv{切换环境}
        SwitchEnv --> |后端本地| LocalEnv[本地联调]
        SwitchEnv --> |Staging| StagingEnv[Staging 联调]
        
        LocalEnv --> |发现问题| Fix[修复问题]
        StagingEnv --> |发现问题| Fix
        Fix --> |接口变更?| APIChange{接口变更?}
        
        APIChange --> |是| UpdateApifox[更新 Apifox 文档]
        UpdateApifox --> Notify[通知前端更新]
        APIChange --> |否| Integration
        
        Notify --> FE_Config
    end

    subgraph 测试层["🧪 7. 测试验证层"]
        Integration --> |通过| TestPhase[测试阶段]
        TestPhase --> ContractTest[契约测试]
        ContractTest --> ApifoxCLI[Apifox CLI 验证]
        ApifoxCLI --> |通过| E2E[E2E 测试]
        ApifoxCLI --> |失败| Fix
        E2E --> |通过| UAT[UAT 验收]
    end

    subgraph 发布层["🚀 8. 发布上线层"]
        UAT --> |通过| Release[发布上线]
        Release --> Monitor[生产监控]
        Monitor --> |发现问题| Hotfix[热修复]
        Hotfix --> |更新接口| UpdateApifox
    end
```

## 详细阶段说明

### 阶段 1: 版本规划

```mermaid
flowchart LR
    subgraph 输入["输入"]
        A1[业务目标]
        A2[用户反馈]
        A3[竞品分析]
    end
    
    subgraph 处理["处理"]
        B1[需求池梳理]
        B2[优先级排序]
        B3[版本规划]
    end
    
    subgraph 输出["输出"]
        C1[PRD 文档]
        C2[版本计划]
        C3[里程碑]
    end
    
    A1 & A2 & A3 --> B1 --> B2 --> B3
    B3 --> C1 & C2 & C3
```

**负责人**: 产品经理
**输出物**:
- PRD 需求文档（功能清单、业务流程）
- 版本路线图
- 迭代计划（Sprint 规划）

### 阶段 2: 需求评审

```mermaid
sequenceDiagram
    participant PM as 产品经理
    participant Dev as 开发组长
    participant FE as 前端代表
    participant BE as 后端代表
    participant QA as 测试代表

    PM ->> Dev: 发起需求评审会
    PM ->> PM: 讲解业务背景
    PM ->> Dev: 演示原型/PRD
    
    Dev ->> PM: 技术可行性评估
    FE ->> PM: 前端工作量评估
    BE ->> PM: 后端工作量评估
    QA ->> PM: 测试范围确认
    
    alt 评审通过
        Dev ->> PM: 确认需求范围
        PM ->> Dev: 进入任务拆分
    else 评审不通过
        Dev ->> PM: 提出修改建议
        PM ->> PM: 调整需求
        PM ->> Dev: 重新评审
    end
```

**关键决策**:
- 哪些需求进入本次迭代
- 技术方案可行性
- 初步工作量评估

### 阶段 3: 任务拆分与派发

```mermaid
flowchart TB
    A[确认需求] --> B[识别依赖]
    B --> C{存在接口依赖?}
    
    C --> |是| D[创建接口设计任务]
    C --> |否| E[直接拆分开发任务]
    
    D --> D1[接口设计任务<br/>负责人: 后端主导]
    D1 --> D2[前置任务<br/>阻塞前后端开发]
    
    E --> E1[前端开发任务]
    E --> E2[后端开发任务]
    E --> E3[UI 设计任务]
    E --> E4[测试用例编写任务]
    
    D2 & E1 & E2 & E3 & E4 --> F[录入项目管理工具]
    F --> G[Jira/飞书/禅道]
    
    G --> H[任务派发]
    H --> |前端任务| FE_Dev[前端开发]
    H --> |后端任务| BE_Dev[后端开发]
    H --> |接口任务| API_Owner[接口负责人]
```

**项目管理工具配置**:
```yaml
任务类型:
  - 接口设计任务:
      负责人: 后端开发
      协作者: 前端开发
      阻塞: 前后端开发任务
      状态: 待设计 → 设计中 → 评审中 → 已完成
      
  - 前端开发任务:
      依赖: 接口设计完成
      触发条件: 接口状态 = 已完成
      
  - 后端开发任务:
      依赖: 接口设计完成
      并行: 可与前端同时进行
```

### 阶段 4: 接口设计（Apifox 核心流程）

```mermaid
flowchart TB
    subgraph 设计阶段["接口设计阶段"]
        A[开始设计] --> B[创建接口]
        B --> C[定义基本信息]
        C --> D[定义请求参数]
        D --> E[定义响应结构]
        E --> F[编写示例数据]
        F --> G[配置 Mock 规则]
        G --> H[保存并生成文档]
    end
    
    subgraph 协作阶段["协作评审阶段"]
        H --> I[分享接口链接]
        I --> J[前端预览]
        I --> K[测试预览]
        J --> L{是否满足需求?}
        K --> L
        L --> |否| M[调整接口]
        M --> B
        L --> |是| N[接口定版]
    end
    
    subgraph 输出阶段["输出生成阶段"]
        N --> O[生成 Mock URL]
        N --> P[更新任务状态]
        N --> Q[通知相关人]
        
        O --> R[前端开始使用]
        P --> S[Jira 状态更新]
        Q --> T[飞书/钉钉通知]
    end
```

**Apifox 操作步骤**:

```markdown
1. 创建接口
   - Path: /api/v1/order/create
   - Method: POST
   - 标签: 订单模块

2. 定义请求
   - Content-Type: application/json
   - Body Schema:
     ```json
     {
       "product_id": { "type": "integer", "required": true },
       "quantity": { "type": "integer", "minimum": 1 }
     }
     ```

3. 定义响应
   - 200: 成功
   - 400: 参数错误
   - 500: 服务器错误
   
4. 编写示例
   - 成功示例: { "code": 200, "data": { "order_id": 123 } }
   - 失败示例: { "code": 400, "message": "库存不足" }

5. 配置 Mock
   - 默认返回成功示例
   - 特定参数返回特定响应（如 quantity=999 返回库存不足）

6. 分享文档
   - 复制文档链接给前端
   - 复制 Mock URL 给前端配置
```

### 阶段 5: 并行开发

```mermaid
flowchart TB
    subgraph 前端开发["前端开发流程"]
        A[接到任务] --> B[查看 Apifox 文档]
        B --> C[配置环境变量]
        C --> D[API_BASE_URL=mock.apifox.com]
        D --> E[启动开发]
        E --> F[对接 Mock 数据]
        F --> G[开发 UI 逻辑]
        G --> H[编写组件测试]
        H --> I[编写契约测试]
        I --> J[提交代码]
    end
    
    subgraph 后端开发["后端开发流程"]
        K[接到任务] --> L[查看接口定义]
        L --> M[创建数据库表]
        M --> N[实现接口逻辑]
        N --> O[编写单元测试]
        O --> P[Postman 自测]
        P --> Q[更新 Apifox 状态]
        Q --> R[标记为已实现]
        R --> S[提交代码]
    end
    
    subgraph 状态同步["状态同步"]
        T[Apifox Webhook]
        U[自动通知前端]
        V[前端看到后端的实现进度]
    end
    
    R --> T --> U --> V
```

**前端配置示例**:
```dart
// lib/config/api_config.dart
class ApiConfig {
  static String get baseUrl {
    const env = String.fromEnvironment('API_ENV', defaultValue: 'mock');
    switch (env) {
      case 'mock':
        // Apifox Mock 服务
        return 'https://mock.apifox.com/m1/xxxxxx/default';
      case 'local':
        // 后端本地服务（联调时用）
        return 'http://localhost:8080';
      case 'staging':
        return 'https://api-staging.ttpos.com';
      default:
        return 'https://api.ttpos.com';
    }
  }
}
```

### 阶段 6: 联调验收

```mermaid
flowchart TB
    A[前后端开发完成] --> B{选择联调环境}
    
    B -->|后端本地| C[后端本地联调]
    B -->|Staging| D[Staging 联调]
    
    C --> E[前端切换至 localhost]
    D --> F[前端切换至 staging]
    
    E --> G[开始联调]
    F --> G
    
    G --> H{发现问题}
    
    H -->|接口不符| I[检查 Apifox 定义]
    I --> J{谁的问题?}
    
    J -->|后端实现错误| K[后端修复]
    J -->|前端调用错误| L[前端修复]
    J -->|接口设计缺陷| M[更新接口设计]
    
    M --> N[重新评审接口]
    N --> O[通知相关人]
    O --> P[前端更新 Mock]
    
    K --> Q[重新联调]
    L --> Q
    P --> Q
    
    H -->|数据问题| R[检查测试数据]
    R --> S[后端补充数据]
    S --> Q
    
    Q --> T{联调通过?}
    T -->|否| H
    T -->|是| U[联调完成]
```

**环境切换命令**:
```bash
# 开发环境（Apifox Mock）
flutter run --dart-define=API_ENV=mock

# 联调环境（后端本地）
flutter run --dart-define=API_ENV=local

# 测试环境（Staging）
flutter run --dart-define=API_ENV=staging

# 生产环境
flutter run --dart-define=API_ENV=prod
```

### 阶段 7-8: 测试与发布

```mermaid
flowchart TB
    A[联调完成] --> B[提交测试]
    
    B --> C[契约测试]
    C --> D[Apifox CLI 运行]
    D --> E{契约一致?}
    E -->|否| F[修复接口差异]
    F --> C
    
    E -->|是| G[运行 Flutter 集成测试]
    G --> H[调用 Mock API]
    H --> I{测试通过?}
    I -->|否| J[修复代码]
    J --> G
    
    I -->|是| K[E2E 测试]
    K --> L[Playwright 测试]
    L --> M{通过?}
    M -->|否| N[修复问题]
    N --> K
    
    M -->|是| O[UAT 验收]
    O --> P{通过?}
    P -->|否| Q[调整优化]
    Q --> O
    
    P -->|是| R[发布上线]
    R --> S[生产监控]
    S --> T{发现问题?}
    T -->|是| U[热修复]
    U --> V[更新 Apifox 文档]
    T -->|否| W[版本完成]
```

## 角色职责矩阵

| 阶段 | 产品经理 | 前端开发 | 后端开发 | 测试工程师 | Apifox 作用 |
|------|----------|----------|----------|------------|-------------|
| 需求规划 | ✅ 主导 | ⚪ 参与 | ⚪ 参与 | ⚪ 了解 | ❌ 无 |
| 需求评审 | ✅ 主讲 | ⚪ 评估 | ⚪ 评估 | ⚪ 评估 | ❌ 无 |
| 任务拆分 | ⚪ 确认 | ⚪ 接收 | ⚪ 接收 | ⚪ 接收 | ❌ 无 |
| **接口设计** | ❌ 不参与 | ⚪ 评审 | **✅ 主导** | ⚪ 了解 | **✅ 核心平台** |
| **Mock 开发** | ❌ 不参与 | **✅ 使用** | ⚪ 维护 | ❌ 不参与 | **✅ 提供 Mock** |
| **接口联调** | ❌ 不参与 | **✅ 参与** | **✅ 参与** | ⚪ 了解 | **✅ 对照标准** |
| 契约测试 | ❌ 不参与 | **✅ 编写** | ⚪ 配合 | **✅ 审核** | **✅ CLI 验证** |
| E2E 测试 | ❌ 不参与 | ⚪ 配合 | ⚪ 配合 | **✅ 主导** | ❌ 无 |
| 发布上线 | ✅ 主导 | ⚪ 配合 | ⚪ 配合 | **✅ 验证** | ❌ 无 |

## 关键检查点

```mermaid
flowchart LR
    A[需求确认] -->|检查| B[接口设计完成?]
    B -->|检查| C[Mock 可用?]
    C -->|检查| D[前后端开发完成?]
    D -->|检查| E[联调通过?]
    E -->|检查| F[契约测试通过?]
    F -->|检查| G[E2E 通过?]
    G -->|检查| H[UAT 通过?]
    H --> I[发布]
    
    style B fill:#ffcccc
    style F fill:#ffcccc
    style C fill:#ccffcc
```

**阻塞点**:
- 🔴 接口设计未完成 → 阻塞前后端开发
- 🔴 契约测试失败 → 阻塞合并代码
- 🟢 Mock 不可用 → 前端可先用静态数据开发

## 工具链整合

```mermaid
flowchart TB
    subgraph 项目管理["项目管理"]
        Jira[Jira/飞书/禅道]
    end
    
    subgraph 接口管理["接口管理"]
        Apifox[Apifox 平台]
    end
    
    subgraph 代码管理["代码管理"]
        Git[GitHub/GitLab]
    end
    
    subgraph CI_CD["CI/CD"]
        Actions[GitHub Actions]
    end
    
    subgraph 沟通["沟通协作"]
        Lark[飞书/钉钉]
    end
    
    Jira -->|创建接口设计任务| Apifox
    Apifox -->|Webhook 状态更新| Jira
    Apifox -->|Mock URL| Git
    Git -->|触发| Actions
    Actions -->|运行契约测试| Apifox
    Actions -->|通知| Lark
    Apifox -->|接口变更通知| Lark
```

---

## 总结

整个流程的关键在于：**接口设计任务作为前后端的阻塞依赖，必须在开发前完成并经过评审。**

Apifox 在整个流程中扮演 **单一事实来源** 的角色：
1. 设计阶段：定义接口规范
2. 开发阶段：提供 Mock 服务
3. 联调阶段：作为对照标准
4. 测试阶段：验证契约合规
