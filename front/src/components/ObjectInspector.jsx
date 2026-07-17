import { Collapse, Descriptions, Space, Tag, Typography } from 'antd'
import { Citation, CitationGroup } from './Citation'

const { Paragraph, Text } = Typography
const CITATION_KEYS = new Set(['case_evidence', 'source_citations', 'guideline_citations', 'guideline_evidence'])

const labelize = (key) => key.replaceAll('_', ' ')

function Primitive({ value }) {
  if (value === null || value === undefined || value === '') return <Text type="secondary">—</Text>
  if (typeof value === 'boolean') return <Tag color={value ? 'success' : 'default'}>{value ? '是' : '否'}</Tag>
  const text = String(value)
  return text.length > 180 ? <Paragraph className="long-value">{text}</Paragraph> : <Text>{text}</Text>
}

function CitationArray({ name, value }) {
  if (name === 'source_citations') return <CitationGroup sourceCitations={value} />
  if (name === 'case_evidence') return <CitationGroup caseEvidence={value} />
  return <CitationGroup refs={value} />
}

function ArrayValue({ name, value, depth }) {
  if (!value.length) return <Text type="secondary">无</Text>
  if (CITATION_KEYS.has(name)) return <CitationArray name={name} value={value} />
  if (value.every((item) => typeof item !== 'object')) return <Space size={[4, 4]} wrap>{value.map((item, index) => <Tag key={index}>{String(item)}</Tag>)}</Space>
  return (
    <div className="object-array">
      {value.map((item, index) => (
        <div className="object-array-item" key={index}>
          <div className="object-array-index">{index + 1}</div>
          <ObjectInspector value={item} depth={depth + 1} />
        </div>
      ))}
    </div>
  )
}

export function ObjectInspector({ value, depth = 0 }) {
  if (Array.isArray(value)) return <ArrayValue name="items" value={value} depth={depth} />
  if (!value || typeof value !== 'object') return <Primitive value={value} />
  const entries = Object.entries(value)
  const simple = entries.filter(([, item]) => item === null || typeof item !== 'object')
  const complex = entries.filter(([, item]) => item && typeof item === 'object')
  const directlyCitable = value.graph_unit_id || value.node_id || value.evidence_ref || value.source_ref
  return (
    <div className={`object-inspector depth-${Math.min(depth, 3)}`}>
      {directlyCitable && <div className="direct-citation"><Citation value={value} /></div>}
      {simple.length > 0 && (
        <Descriptions size="small" column={1} colon={false} className="object-descriptions">
          {simple.map(([name, item]) => (
            <Descriptions.Item key={name} label={labelize(name)}><Primitive value={item} /></Descriptions.Item>
          ))}
        </Descriptions>
      )}
      {complex.map(([name, item]) => (
        <div className="complex-field" key={name}>
          <div className="field-label">{labelize(name)}</div>
          {Array.isArray(item)
            ? <ArrayValue name={name} value={item} depth={depth} />
            : depth < 2
              ? <ObjectInspector value={item} depth={depth + 1} />
              : <Collapse ghost size="small" items={[{ key: name, label: '展开结构', children: <ObjectInspector value={item} depth={depth + 1} /> }]} />}
        </div>
      ))}
    </div>
  )
}
