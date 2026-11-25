import React from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'

import { App } from './App'
import './index.css'
import { Analysis } from './pages/Analysis'
import { Dashboard } from './pages/Dashboard'
import { Exports } from './pages/Exports'
import { Highlights } from './pages/Highlights'
import { Ingest } from './pages/Ingest'
import { JobProgress } from './pages/JobProgress'
import { Review } from './pages/Review'
import { Settings } from './pages/Settings'

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'import', element: <Ingest /> },
      { path: 'analysis', element: <Analysis /> },
      { path: 'highlights', element: <Highlights /> },
      { path: 'exports', element: <Exports /> },
      { path: 'jobs/:jobId', element: <JobProgress /> },
      { path: 'jobs/:jobId/clips', element: <Review /> },
      { path: 'settings', element: <Settings /> },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)
