import { createContext, useContext, useEffect, useState } from 'react'

// Shared "unsaved changes" flag. The Settings page raises this while its form
// differs from the last-saved baseline; the sidebar (App.jsx) reads it to warn
// before an in-app navigation drops those edits, and we register a
// beforeunload handler here so a tab close / reload warns too.
const UnsavedChangesContext = createContext({ dirty: false, setDirty: () => {} })

export function UnsavedChangesProvider({ children }) {
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!dirty) return
    const handler = (e) => {
      // Chrome requires both preventDefault and a returnValue assignment to
      // trigger its native "Leave site?" prompt.
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  return (
    <UnsavedChangesContext.Provider value={{ dirty, setDirty }}>
      {children}
    </UnsavedChangesContext.Provider>
  )
}

export function useUnsavedChanges() {
  return useContext(UnsavedChangesContext)
}
