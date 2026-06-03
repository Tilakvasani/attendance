export default function Card({ title, children, className = "" }) {
  return (
    <div className={`bg-[#1a1f2e] border border-[#2a2f45] rounded-xl p-5 ${className}`}>
      {title && (
        <h2 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-3">
          {title}
        </h2>
      )}
      {children}
    </div>
  );
}
