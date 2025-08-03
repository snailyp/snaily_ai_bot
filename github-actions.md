# GitHub Actions CI/CD 配置指南

本文档详细介绍项目中配置的 GitHub Actions 工作流程，用于自动构建和推送 Docker 镜像到 GitHub Container Registry (GHCR)。

## 📋 工作流程概览

项目包含三个主要的 GitHub Actions 工作流程：

1. **构建和推送** (`.github/workflows/build-and-push.yml`) - 主要的 CI/CD 流程
2. **版本发布** (`.github/workflows/release.yml`) - 专门用于版本发布
3. **镜像清理** (`.github/workflows/cleanup.yml`) - 定期清理旧版本镜像

## 🔄 构建和推送工作流程

### 触发条件

- **推送到主分支** (`main`) - 构建 `latest` 标签
- **推送到开发分支** (`develop`) - 构建 `develop` 标签
- **推送标签** (`v*`) - 构建对应版本标签
- **Pull Request** - 仅构建不推送（用于测试）
- **手动触发** - 可在 Actions 页面手动运行

### 工作流程步骤

1. **检出代码** - 获取最新的源代码
2. **设置 Docker Buildx** - 启用多架构构建支持
3. **登录 GHCR** - 使用 GitHub Token 自动登录
4. **提取元数据** - 生成镜像标签和标签
5. **构建并推送** - 构建多架构镜像并推送到 GHCR
6. **生成摘要** - 在 Actions 页面显示构建结果

### 支持的架构

- `linux/amd64` - Intel/AMD 64位处理器
- `linux/arm64` - ARM 64位处理器（Apple Silicon、树莓派4等）

### 标签策略

```yaml
# 分支策略
type=ref,event=branch          # main, develop
type=ref,event=pr              # pr-123

# 标签策略  
type=semver,pattern={{version}}      # 1.0.0
type=semver,pattern={{major}}.{{minor}} # 1.0
type=semver,pattern={{major}}        # 1

# 特殊标签
type=raw,value=latest,enable={{is_default_branch}}  # latest (仅主分支)
type=raw,value=develop,enable=${{ github.ref == 'refs/heads/develop' }}
```

## 🎉 版本发布工作流程

### 触发条件

- **发布 Release** - 在 GitHub 上创建新的 Release
- **手动触发** - 可指定版本号手动运行

### 特殊功能

- **扩展架构支持** - 额外支持 `linux/arm/v7` 架构
- **版本信息注入** - 将版本号和构建时间注入到镜像中
- **多标签生成** - 自动生成多个版本标签

### 构建参数

```dockerfile
ARG VERSION="dev"           # 版本号
ARG BUILD_DATE             # 构建时间
ARG TARGETPLATFORM         # 目标平台
ARG BUILDPLATFORM          # 构建平台
```

## 🧹 镜像清理工作流程

### 触发条件

- **定时执行** - 每周日凌晨2点自动运行
- **手动触发** - 可指定保留版本数量

### 清理策略

- 默认保留最新的 **10** 个版本
- 优先保留带语义版本号的标签
- 保留 `latest` 和 `develop` 标签

## ⚙️ 配置和设置

### 必需的权限

在仓库设置中确保 Actions 具有以下权限：

```yaml
permissions:
  contents: read      # 读取仓库内容
  packages: write     # 写入包到 GHCR
```

### 环境变量

工作流程使用以下环境变量：

```yaml
env:
  REGISTRY: ghcr.io                    # 容器注册表地址
  IMAGE_NAME: ${{ github.repository }} # 镜像名称（自动获取）
```

### Secrets 配置

工作流程使用内置的 `GITHUB_TOKEN`，无需额外配置 Secrets。

## 🚀 使用指南

### 1. 启用 GitHub Container Registry

1. 进入仓库的 **Settings** 页面
2. 在左侧菜单中选择 **Actions** > **General**
3. 在 **Workflow permissions** 部分选择 **Read and write permissions**
4. 勾选 **Allow GitHub Actions to create and approve pull requests**

### 2. 推送代码触发构建

```bash
# 推送到主分支 - 构建 latest 标签
git push origin main

# 推送到开发分支 - 构建 develop 标签  
git push origin develop

# 推送标签 - 构建版本标签
git tag v1.0.0
git push origin v1.0.0
```

### 3. 创建 Release 触发发布

1. 在 GitHub 仓库页面点击 **Releases**
2. 点击 **Create a new release**
3. 填写标签版本（如 `v1.0.0`）
4. 填写发布说明
5. 点击 **Publish release**

### 4. 手动触发工作流程

1. 进入仓库的 **Actions** 页面
2. 选择要运行的工作流程
3. 点击 **Run workflow**
4. 填写必要的参数（如果有）
5. 点击 **Run workflow** 确认

## 📊 监控和调试

### 查看构建状态

1. 进入仓库的 **Actions** 页面
2. 选择对应的工作流程运行
3. 查看各个步骤的执行状态和日志

### 常见问题排查

#### 1. 权限错误

```
Error: denied: permission_denied
```

**解决方案：**
- 检查仓库的 Actions 权限设置
- 确保启用了 **Read and write permissions**

#### 2. 镜像推送失败

```
Error: failed to push to registry
```

**解决方案：**
- 检查 GHCR 是否启用
- 验证 `GITHUB_TOKEN` 权限
- 确认镜像名称格式正确

#### 3. 多架构构建失败

```
Error: failed to build for platform linux/arm64
```

**解决方案：**
- 检查 Dockerfile 是否支持多架构
- 验证依赖包是否有对应架构版本
- 考虑使用条件构建

### 调试技巧

1. **启用调试日志**
   ```yaml
   - name: 启用调试
     run: echo "ACTIONS_STEP_DEBUG=true" >> $GITHUB_ENV
   ```

2. **查看构建上下文**
   ```yaml
   - name: 显示构建信息
     run: |
       echo "Repository: ${{ github.repository }}"
       echo "Ref: ${{ github.ref }}"
       echo "Event: ${{ github.event_name }}"
   ```

3. **测试 Docker 构建**
   ```yaml
   - name: 测试构建
     run: |
       docker build --platform linux/amd64 -t test-image .
       docker run --rm test-image python --version
   ```

## 🔧 自定义配置

### 修改支持的架构

在 `build-and-push.yml` 中修改：

```yaml
platforms: linux/amd64,linux/arm64,linux/arm/v7
```

### 自定义标签策略

在 `docker/metadata-action` 步骤中修改：

```yaml
tags: |
  type=ref,event=branch
  type=ref,event=pr
  type=semver,pattern={{version}}
  type=raw,value=stable,enable={{is_default_branch}}
```

### 添加构建参数

```yaml
build-args: |
  VERSION=${{ steps.version.outputs.version }}
  BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
  CUSTOM_ARG=value
```

### 配置缓存策略

```yaml
cache-from: type=gha
cache-to: type=gha,mode=max
```

## 📈 最佳实践

1. **使用语义版本号** - 遵循 `v1.0.0` 格式
2. **编写清晰的提交信息** - 便于生成 Release Notes
3. **定期清理旧镜像** - 节省存储空间
4. **监控构建时间** - 优化 Dockerfile 和依赖
5. **测试多架构兼容性** - 确保在不同平台正常运行
6. **使用构建缓存** - 加速构建过程
7. **设置适当的标签** - 便于镜像管理和部署

## 🔗 相关资源

- [GitHub Actions 文档](https://docs.github.com/en/actions)
- [Docker Buildx 文档](https://docs.docker.com/buildx/)
- [GitHub Container Registry 文档](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [Docker 多架构构建指南](https://docs.docker.com/build/building/multi-platform/)

## 📝 更新日志

### v1.0.0
- ✅ 基础 CI/CD 流程
- ✅ 多架构构建支持
- ✅ 自动版本标签
- ✅ GHCR 集成
- ✅ 镜像清理机制