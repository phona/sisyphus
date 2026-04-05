# Apifox 契约测试完整工作流

## 整体架构

```mermaid
flowchart TB
    subgraph 协作层["🤝 团队协作层"]
        A[Apifox 平台] --> B[API 文档]
        A --> C[Mock 服务]
        A --> D[契约定义]
        B --> E[前端开发]
        B --> F[后端开发]
        D --> F
    end

    subgraph 开发层["💻 开发层"]
        E --> G[Flutter App]
        F --> H[后端服务]
        C --> I[本地开发]
        G --> I
    end

    subgraph 测试层["🧪 测试层"]
        G --> J[契约测试]
        C --> J
        G --> K[E2E 测试]
        H --> K
    end

    subgraph 部署层["🚀 部署层"]
        J --> L[CI 检查]
        K --> M[发布前验证]
        L --> N[Staging]
        M --> O[Production]
    end
```

## 详细工作流程

```mermaid
sequenceDiagram
    participant PM as 产品经理
    participant FE as 前端开发
    participant BE as 后端开发
    participant AF as Apifox 平台
    participant CI as CI/CD

    Note over PM,CI: 阶段 1: API 设计
    PM ->> FE: 需求文档
    PM ->> BE: 需求文档
    FE ->> AF: 定义 API 接口
    BE ->> AF: 确认接口规范
    AF ->> FE: 生成 Mock URL
    AF ->> BE: 生成接口文档

    Note over PM,CI: 阶段 2: 并行开发
    par 前端开发
        FE ->> FE: 配置 Mock URL
        loop 本地开发
            FE ->> AF: 请求 Mock 数据
            AF -->> FE: 返回预定义响应
            FE ->> FE: 开发 UI/逻辑
        end
    and 后端开发
        BE ->> BE: 实现 API 逻辑
        BE ->> AF: 更新实现状态
    end

    Note over PM,CI: 阶段 3: 契约测试
    FE ->> FE: 编写契约测试
    loop 测试执行
        FE ->> AF: 契约验证请求
        AF -->> FE: 返回响应
        FE ->> FE: 验证响应格式
    end

    Note over PM,CI: 阶段 4: 联调
    FE ->> BE: 切换至后端服务
    BE -->> FE: 真实 API 响应
    alt 契约不一致
        FE ->> AF: 标记接口变更
        BE ->> AF: 更新接口定义
    end

    Note over PM,CI: 阶段 5: CI/CD
    FE ->> CI: 提交代码
    CI ->> AF: 执行契约测试
    AF -->> CI: 测试报告
    alt 契约测试通过
        CI ->> CI: 运行 E2E 测试
        CI ->> CI: 部署 Staging
    else 契约测试失败
        CI -->> FE: 阻止合并
        FE ->> AF: 检查接口变更
    end
```

## 开发工作流

```mermaid
flowchart LR
    subgraph 每日开发["📅 每日开发循环"]
        A[启动开发] --> B{选择环境}
        B -->|新功能| C[Apifox Mock]
        B -->|Bug 修复| D[后端本地]
        B -->|集成验证| E[Staging]
        
        C --> F[编码]
        D --> F
        F --> G[本地测试]
        G -->|通过| H[提交代码]
        G -->|失败| I[检查契约]
        I --> J[更新 Mock/代码]
        J --> F
        
        H --> K[CI 运行契约测试]
        K -->|通过| L[代码审查]
        K -->|失败| M[修复问题]
        M --> H
    end
```

## 测试金字塔（Apifox 版）

```mermaid
flowchart TB
    subgraph 测试策略["🧪 测试策略"]
        direction TB
        
        E2E["L3: E2E 测试<br/>Playwright<br/>运行频率: 发布前<br/>数量: 3-5 个<br/>环境: Staging"]
        
        Contract["L2: 契约测试<br/>Apifox CLI + Flutter<br/>运行频率: 每次 PR<br/>数量: 10-15 个<br/>环境: Mock"]
        
        Unit["L1: 单元测试<br/>Flutter Test<br/>运行频率: 可选<br/>数量: 关键算法<br/>环境: 本地"]
        
        Unit --> Contract --> E2E
    end

    subgraph 运行时["⏱️ 运行时"]
        E2E -->|30-60s/个| R3[慢但全面]
        Contract -->|2-5s/个| R2[中等速度]
        Unit -->|<1s/个| R1[极速反馈]
    end
```

## CI/CD 流水线

```mermaid
flowchart LR
    A[代码提交] --> B[代码检查]
    B --> C[单元测试]
    C --> D[契约测试]
    
    subgraph 契约测试详情["契约测试阶段"]
        D1[Apifox CLI<br/>验证 API 契约] --> D2[Flutter 集成测试<br/>调用 Mock API]
        D2 --> D3{全部通过?}
    end
    
    D --> D1
    D3 -->|是| E[构建应用]
    D3 -->|否| F[阻止合并]
    
    E --> G[部署 Staging]
    G --> H[E2E 测试]
    H --> I{通过?}
    I -->|是| J[部署 Production]
    I -->|否| K[回滚/修复]
    
    style F fill:#ffcccc
    style J fill:#ccffcc
```

## 环境切换策略

```mermaid
flowchart TB
    subgraph 环境矩阵["🌍 环境切换矩阵"]
        direction TB
        
        Dev["开发环境"] --> DevMock["Apifox Mock<br/>https://mock.apifox.com/xxx"]
        Dev --> DevLocal["后端本地<br/>http://localhost:8080"]
        
        Test["测试环境"] --> TestMock["Apifox Mock<br/>契约验证"]
        Test --> TestStaging["Staging<br/>集成测试"]
        
        Prod["生产环境"] --> ProdReal["真实 API<br/>监控告警"]
    end

    subgraph 切换规则["🔄 切换规则"]
        R1["开发新功能 → Apifox Mock"] 
        R2["后端联调 → 后端本地"]
        R3["CI 测试 → Apifox Mock"]
        R4["发布前 → Staging"]
        R5["生产 → 真实 API"]
    end
```

## 角色职责

```mermaid
flowchart TB
    subgraph 前端开发["👨‍💻 前端开发"]
        FE1[查看 API 文档<br/>Apifox 平台]
        FE2[配置 Mock URL<br/>开发调试]
        FE3[编写契约测试<br/>验证响应格式]
        FE4[运行测试<br/>flutter test]
        
        FE1 --> FE2 --> FE3 --> FE4
    end

    subgraph 后端开发["👨‍💻 后端开发"]
        BE1[实现 API<br/>按契约开发]
        BE2[更新实现状态<br/>Apifox 标记]
        BE3[通知前端<br/>联调准备]
        
        BE1 --> BE2 --> BE3
    end

    subgraph 测试/QA["🧪 测试/QA"]
        QA1[编写 E2E 测试<br/>Playwright]
        QA2[验证契约<br/>Apifox CLI]
        QA3[Staging 验收<br/>发布前检查]
        
        QA1 --> QA2 --> QA3
    end

    subgraph 自动化["🤖 CI/CD"]
        CI1[拉取代码]
        CI2[运行 Apifox CLI<br/>契约验证]
        CI3[运行 Flutter 契约测试]
        CI4[运行 E2E 测试]
        CI5[部署/阻止]
        
        CI1 --> CI2 --> CI3 --> CI4 --> CI5
    end
```

## 问题排查流程

```mermaid
flowchart TD
    A[测试失败] --> B{失败类型}
    
    B -->|契约测试失败| C[检查 Apifox Mock]
    C --> C1[Mock 是否正确?]
    C1 -->|否| C2[更新 Mock 配置]
    C1 -->|是| C3[检查请求格式]
    C3 -->|问题在前端| D[修复前端代码]
    C3 -->|问题在后端| E[通知后端更新]
    
    B -->|E2E 测试失败| F[检查 Staging 环境]
    F --> F1[API 是否正常?]
    F1 -->|否| G[后端修复]
    F1 -->|是| H[UI 是否有变化?]
    H -->|是| I[更新 E2E 测试]
    H -->|否| J[检查测试数据]
    
    B -->|Apifox CLI 失败| K[检查 API 变更]
    K --> K1[契约是否变更?]
    K1 -->|是| L[同步更新契约测试]
    K1 -->|否| M[检查网络连接]

    style D fill:#ccffcc
    style E fill:#ffcccc
    style G fill:#ffcccc
    style I fill:#ccffcc
```

## 完整命令参考

```mermaid
flowchart LR
    subgraph 常用命令["📝 常用命令"]
        C1["flutter run<br/>--dart-define=API_BASE_URL=mock.apifox.com"]
        C2["flutter test test/integration/<br/>--dart-define=API_BASE_URL=mock.apifox.com"]
        C3["apifox-cli run<br/>--project-id=xxx --environment=mock"]
        C4["npx playwright test<br/>--project=staging"]
    end

    subgraph 使用场景["使用场景"]
        S1["日常开发"]
        S2["本地测试"]
        S3["CI 检查"]
        S4["发布前验证"]
    end

    S1 --> C1
    S2 --> C2
    S3 --> C3
    S4 --> C4
```

---

## 工作流总结

| 阶段 | 工具 | 输出 | 责任人 |
|------|------|------|--------|
| API 设计 | Apifox | 接口文档 + Mock | 前后端协商 |
| 并行开发 | Apifox Mock | 可运行的前端 | 前端开发 |
| 契约测试 | Flutter + Apifox | 测试报告 | 前端开发 |
| CI 验证 | Apifox CLI | 通过/失败 | 自动化 |
| E2E 测试 | Playwright | 发布许可 | QA/自动化 |
| 部署 | GitHub Actions | 上线 | 自动化 |
