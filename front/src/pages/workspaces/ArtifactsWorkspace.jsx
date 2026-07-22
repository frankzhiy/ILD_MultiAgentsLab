import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import Editor from '@monaco-editor/react'
import { Button, Card, Input, Space, Spin, Tag, Typography } from 'antd'
import { DownloadOutlined, FileOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '../../api'
import { QueryError } from '../../components/QueryState'

const { Text, Title } = Typography

function ArtifactPreview({ runId, artifact }) {
  const textual = ['json', 'txt', 'yaml', 'yml', 'md', 'log'].includes(artifact?.kind)
  const query = useQuery({
    queryKey: ['artifact-text', runId, artifact?.name],
    queryFn: () => api.artifactText(runId, artifact.name),
    enabled: Boolean(artifact && textual),
  })
  if (!artifact) return <div className="artifact-placeholder"><FileOutlined /><Text type="secondary">选择左侧产物进行检查</Text></div>
  const url = api.artifactUrl(runId, artifact.name)
  if (artifact.kind === 'html' || artifact.kind === 'pdf') return <iframe title={artifact.name} className="artifact-frame" src={url} />
  if (!textual) return <div className="artifact-placeholder"><FileOutlined /><Text>该二进制产物请在新窗口打开。</Text><Button href={url} target="_blank">打开文件</Button></div>
  if (query.isLoading) return <div className="center-spin"><Spin /></div>
  if (query.isError) return <QueryError error={query.error} retry={query.refetch} />
  return <Editor height="100%" language={artifact.kind === 'json' ? 'json' : artifact.kind === 'yaml' || artifact.kind === 'yml' ? 'yaml' : 'plaintext'} value={query.data || ''} options={{ readOnly: true, minimap: { enabled: false }, wordWrap: 'on', fontSize: 12, scrollBeyondLastLine: false }} theme="vs" />
}

export function ArtifactsWorkspace({ runId }) {
  const query = useQuery({ queryKey: ['artifacts', runId], queryFn: () => api.artifacts(runId) })
  const [search, setSearch] = useState('')
  const [selectedName, setSelectedName] = useState(null)
  const artifacts = (query.data || []).filter((item) => !search || item.name.toLowerCase().includes(search.toLowerCase()))
  useEffect(() => {
    if (!selectedName && artifacts.length) setSelectedName(artifacts.find((item) => /_(pulmonology|thoracic_radiology|rheumatology|pathology)_initial\.json$/.test(item.name))?.name || artifacts[0].name)
  }, [artifacts, selectedName])
  if (query.isError) return <QueryError error={query.error} retry={query.refetch} />
  const selected = query.data?.find((item) => item.name === selectedName)
  return (
    <div>
      <div className="workspace-title"><div><Text className="eyebrow">ARTIFACT AUDIT</Text><Title level={3}>产物审计</Title></div><Space><Tag>{query.data?.length || 0} files</Tag>{selected && <Button icon={<DownloadOutlined />} href={api.artifactUrl(runId, selected.name)} target="_blank">打开原文件</Button>}</Space></div>
      <div className="artifact-grid">
        <Card size="small" title="运行文件" className="panel-card artifact-list-card" extra={<Tag>{artifacts.length}</Tag>}>
          <Input prefix={<SearchOutlined />} allowClear placeholder="文件名筛选" value={search} onChange={(event) => setSearch(event.target.value)} />
          <div className="artifact-list">{query.isLoading ? <Spin /> : artifacts.map((item) => <div key={item.name} className={`artifact-item ${selectedName === item.name ? 'selected-artifact' : ''}`} onClick={() => setSelectedName(item.name)}><FileOutlined /><div className="artifact-item-text"><Text ellipsis title={item.name}>{item.name}</Text><Text type="secondary">{item.kind.toUpperCase()} · {(item.bytes / 1024).toFixed(1)} KB</Text></div></div>)}</div>
        </Card>
        <Card size="small" title={selected?.name || '产物预览'} className="panel-card artifact-preview-card" bodyStyle={{ height: 'calc(100% - 46px)', padding: 0 }}><ArtifactPreview runId={runId} artifact={selected} /></Card>
      </div>
    </div>
  )
}
