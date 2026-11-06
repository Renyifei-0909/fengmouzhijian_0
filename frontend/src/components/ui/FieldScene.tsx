const fieldScenes = {
  survey: { file: "route-survey.webp", title: "路由定位", alt: "通信施工人员沿人行道核对路由和工点" },
  capture: { file: "field-capture-guide.webp", title: "工点全貌", alt: "作业人员从围挡外拍摄工点及周边环境" },
  recheck: { file: "remediation-capture-guide.webp", title: "整改后取景", alt: "作业人员拍摄修复后的步道、井盖及周边恢复范围" },
} as const;

export function FieldScene({ scene, guidance }: { scene: keyof typeof fieldScenes; guidance?: string }) {
  const { file, title, alt } = fieldScenes[scene];
  return <figure className="fm-field-scene">
    <img src={`${import.meta.env.BASE_URL}scenes/${file}`} alt={alt} width={1040} height={585} loading="lazy" decoding="async" />
    <figcaption><strong>{title}</strong>{guidance && <p>{guidance}</p>}</figcaption>
  </figure>;
}
