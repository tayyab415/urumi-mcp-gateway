import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import styles from "./Markdown.module.css";

// Chat replies are GitHub-flavoured Markdown: tables, headings, code blocks, links.
// react-markdown builds React elements and ignores raw HTML, so a reply cannot inject markup.
const components: Components = {
  table: ({ node: _, ...props }) => <div className={styles.tableWrap}><table {...props} /></div>,
  a: ({ node: _, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
};

export function Markdown({ text }: { text: string }) {
  return (
    <div className={styles.markdown}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{text}</ReactMarkdown>
    </div>
  );
}
