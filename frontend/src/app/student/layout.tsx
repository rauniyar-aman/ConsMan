import type {Metadata} from 'next';
export const metadata:Metadata={title:'Student progress',robots:{index:false,follow:false},referrer:'no-referrer'};
export default function StudentLayout({children}:{children:React.ReactNode}){return children;}
