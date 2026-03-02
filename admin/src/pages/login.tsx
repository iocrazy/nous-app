import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Form, Input, Button, Message, Card, Typography } from '@arco-design/web-react'
import { IconEmail, IconLock } from '@arco-design/web-react/icon'
import { useAuth } from '../auth/AuthProvider'

const { Title, Text } = Typography

export function Login() {
  const [loading, setLoading] = useState(false)
  const { login, user } = useAuth()
  const navigate = useNavigate()

  // Redirect to dashboard if already authenticated (handles late onAuthStateChange)
  useEffect(() => {
    if (user) navigate('/', { replace: true })
  }, [user, navigate])

  const handleSubmit = async (values: { email: string; password: string }) => {
    setLoading(true)
    const result = await login(values.email, values.password)
    setLoading(false)

    if (result.error) {
      Message.error(result.error)
    } else {
      navigate('/')
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#1d2129',
      }}
    >
      <Card style={{ width: 400, borderRadius: 8 }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <Title heading={4} style={{ margin: 0 }}>
            MediaHub Admin
          </Title>
          <Text type="secondary" style={{ marginTop: 8, display: 'block' }}>
            Sign in to admin panel
          </Text>
        </div>

        <Form layout="vertical" onSubmit={handleSubmit} autoComplete="off">
          <Form.Item
            label="Email"
            field="email"
            rules={[{ required: true, message: 'Please enter your email' }]}
          >
            <Input
              type="email"
              placeholder="admin@example.com"
              prefix={<IconEmail />}
            />
          </Form.Item>

          <Form.Item
            label="Password"
            field="password"
            rules={[{ required: true, message: 'Please enter your password' }]}
          >
            <Input.Password
              placeholder="Enter password"
              prefix={<IconLock />}
            />
          </Form.Item>

          <Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              long
              loading={loading}
            >
              Sign In
            </Button>
          </Form.Item>
        </Form>

        <Text
          type="secondary"
          style={{ display: 'block', textAlign: 'center', fontSize: 13 }}
        >
          Only admin accounts can access this panel
        </Text>
      </Card>
    </div>
  )
}
