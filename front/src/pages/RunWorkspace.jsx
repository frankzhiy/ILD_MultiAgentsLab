import { useQuery } from '@tanstack/react-query'
import {
  ApartmentOutlined, AuditOutlined, BugOutlined, DatabaseOutlined, FileSearchOutlined,
  HomeOutlined, MedicineBoxOutlined, MenuFoldOutlined, MenuUnfoldOutlined,
  ShareAltOutlined,
} from '@ant-design/icons'
import { Button, Layout, Menu, Skeleton, Space, Typography } from 'antd'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { Brand } from '../components/Brand'
import { QueryError } from '../components/QueryState'
import { StatusTag } from '../components/StatusTag'
import { lazy, Suspense, useState } from 'react'

const OverviewWorkspace = lazy(() => import('./workspaces/OverviewWorkspace').then((module) => ({ default: module.OverviewWorkspace })))
const SemanticWorkspace = lazy(() => import('./workspaces/SemanticWorkspace').then((module) => ({ default: module.SemanticWorkspace })))
const RoutingWorkspace = lazy(() => import('./workspaces/RoutingWorkspace').then((module) => ({ default: module.RoutingWorkspace })))
const SpecialtyWorkspace = lazy(() => import('./workspaces/SpecialtyWorkspace').then((module) => ({ default: module.SpecialtyWorkspace })))
const ChairWorkspace = lazy(() => import('./workspaces/ChairWorkspace').then((module) => ({ default: module.ChairWorkspace })))
const ArtifactsWorkspace = lazy(() => import('./workspaces/ArtifactsWorkspace').then((module) => ({ default: module.ArtifactsWorkspace })))
const ErrorWorkspace = lazy(() => import('./workspaces/ErrorWorkspace').then((module) => ({ default: module.ErrorWorkspace })))

const { Header, Sider, Content } = Layout
const { Text } = Typography

const ITEMS = [
  ['overview', <HomeOutlined />, '运行总览'],
  ['semantic', <ApartmentOutlined />, '语义图构建'],
  ['routing', <ShareAltOutlined />, '证据分发'],
  ['specialties', <MedicineBoxOutlined />, '专科工作区'],
  ['chair', <AuditOutlined />, '主持人汇总'],
  ['artifacts', <DatabaseOutlined />, '产物审计'],
  ['errors', <BugOutlined />, '错误与诊断'],
]

const WORKSPACES = {
  overview: OverviewWorkspace,
  semantic: SemanticWorkspace,
  routing: RoutingWorkspace,
  specialties: SpecialtyWorkspace,
  chair: ChairWorkspace,
  artifacts: ArtifactsWorkspace,
  errors: ErrorWorkspace,
}

export function RunWorkspace() {
  const { runId, view } = useParams()
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(false)
  const activeView = view || 'overview'
  const query = useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.run(runId),
    refetchInterval: (current) => ['completed', 'failed', 'cancelled'].includes(current.state.data?.status) ? false : 2500,
  })
  if (!view) return <Navigate to={`/runs/${encodeURIComponent(runId)}/overview`} replace />
  if (query.isError) return <QueryError error={query.error} retry={query.refetch} title="无法打开运行" />
  const Workspace = WORKSPACES[activeView] || OverviewWorkspace
  return (
    <Layout className="workspace-layout">
      <Sider width={244} collapsedWidth={72} collapsed={collapsed} theme="light" className="workspace-sider">
        <div className="workspace-brand"><Brand /></div>
        <Menu
          mode="inline"
          selectedKeys={[activeView]}
          items={ITEMS.map(([key, icon, label]) => ({ key, icon, label }))}
          onClick={({ key }) => navigate(`/runs/${encodeURIComponent(runId)}/${key}`)}
        />
        <div className="sider-footer"><Link to="/runs"><FileSearchOutlined />{!collapsed && <span>全部运行</span>}</Link></div>
      </Sider>
      <Layout>
        <Header className="workspace-header">
          <Space size="middle">
            <Button type="text" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed((value) => !value)} />
            {query.isLoading ? <Skeleton.Input active size="small" /> : <><Text strong>{query.data.case_id}</Text><Text code className="run-code">{runId}</Text><StatusTag status={query.data.status} /></>}
          </Space>
          <Space><Text type="secondary">本地科研运行</Text></Space>
        </Header>
        <Content className="workspace-content"><Suspense fallback={<Skeleton active paragraph={{ rows: 8 }} />}><Workspace runId={runId} run={query.data} /></Suspense></Content>
      </Layout>
    </Layout>
  )
}
