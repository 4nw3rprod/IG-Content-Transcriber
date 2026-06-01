import { Toaster as Sonner, type ToasterProps } from "sonner"

function Toaster(props: ToasterProps) {
  return <Sonner theme="light" richColors closeButton {...props} />
}

export { Toaster }
