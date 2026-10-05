// "$X to go" under a goal bar — or "+$X over goal" once it's passed, instead of
// the old "$-4,033 to go".
export default function GoalGap({ remaining }) {
  const amt = Math.abs(Math.round(remaining)).toLocaleString()
  if (remaining > 0) {
    return <span className="text-gray-500 dark:text-gray-600">${amt} to go</span>
  }
  return <span className="text-green-500">+${amt} over goal</span>
}
