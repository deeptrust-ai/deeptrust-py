import '@livekit/components-styles'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

// No StrictMode on purpose. Its dev double-invoke tears down LiveKit's Room
// between the two mounts, which fires onDisconnected and drops the call.
createRoot(document.getElementById('root')!).render(<App />)
