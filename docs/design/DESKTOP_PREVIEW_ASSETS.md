# 桌面预览素材与地图来源

原赛题的子赛题 5 和咨询回复以通信工程施工监管为主，重点是 QGIS 设计与施工效果的核对、影像采集、整改复验和交付档案。咨询回复允许根据团队技术条件设置施工工序和验证场景。杭州滨江、浦沿、西兴的记录用于本地流程验证。原有德赖艾希 QGIS 工程图层按其原始地理范围单独显示，组委会的 BAKTI 原始影像仍保存在材料目录。

杭州底图从开放街图的区域数据提取，道路、水体、绿地和地名在 MapLibre 中按工作台配色绘制。区域外采用开放街图在线街道瓦片。工程对象、工单坐标及采集坐标仍走现有 WGS84 工程数据与定位规则。[数据提取与许可](../../frontend/public/maps/README.md)

五张工程图片使用内置 ImageGen 生成。取景图用于相应业务位置，本地流程记录上传独立的图像文件并保留其来源字段。Logo 使用原图作为编辑目标去除深色底边，原始文件保留。总览原横幅与左栏原底图继续使用现有素材。

## 生成指令与文件

### 道路通信施工作业

[field-street.webp](../../frontend/public/scenes/field-street.webp)

```text
Use case: photorealistic-natural. Project website background asset for a Chinese urban telecom works operations dashboard. Wide 16:9 horizontal documentary photo, taken at eye level on a normal cloudy workday in a Jiangnan city such as Hangzhou. Roadside utility works are cordoned off along a tree-lined urban road, middle-distance two workers in ordinary muted orange reflective vests and white hard hats look at a route plan beside a modest utility van and road barriers, modern everyday Chinese mixed-use buildings and mature street trees. No visible faces, no readable text, no place names or logos. Strong natural sense of the whole worksite, calm objective framing and realistic scale, restrained cool gray blue palette and warm small details. Main site in center-right, open darkish road and trees on the left suitable for interface text overlay. Practical field photography, no CGI, no neon, no futuristic elements, no photomontage, no dramatic color grade. No bare pipe in center.
```

### 沟槽回填前采集

[duct-inspection.webp](../../frontend/public/scenes/duct-inspection.webp)

```text
Use case: photorealistic-natural. A single wide horizontal reference photograph for a professional Chinese telecom-construction inspection form, 16:9. Camera stands outside protective temporary guard rail, safe oblique angle along a small roadside communication duct trench at a Jiangnan urban job site; foreground edge has realistic bedding sand, restrained unobtrusive small bundled orange communication sleeves already laid at the lower edge and partly obscured, one worker in high-visibility vest and hard hat kneels from a safe stance checking a folding survey rule with their back facing the camera; sidewalk, cones, compact soil and tree roots provide the overall environment. The photo is about the whole operation, with worker and route contextualized; no oversized or isolated bare pipe, no spectacular cutaway, no impossible perspective. Diffused daylight, realistic Chinese site and tools, incidental unposed shot at 35mm, no readable text, no logo, no labels, no timestamp overlay, no video HUD.
```

### 路面恢复与交接

[street-restoration.webp](../../frontend/public/scenes/street-restoration.webp)

```text
Use case: photorealistic-natural. Wide 16:9 horizontal documentary engineering reference image for a Chinese telecom remediation page, independent single photograph. Hangzhou-like ordinary Jiangnan neighborhood streetscape from a diagonal curb perspective after a small communication-duct site has been tidied. Neat recently restored rectangular sidewalk pavers, ordinary flush utility handhole cover at medium distance, temporary folding yellow work barriers near the curb, one safety-vested technician in background doing a final visual walk, building frontage and trees. Full scene establishes how the finished road surface sits with surrounding sidewalk and street, understated, dust and modest construction details, authentic proportional scale. No split-screen before/after, no centered utility product closeup, no bare pipeline, no readable text, no text in graphics, no logo. Overcast natural colors and low visual drama.
```

### Logo 透明边缘

[app-logo-integrated.webp](../../frontend/public/brand/app-logo-integrated.webp)

```text
Use case: precise-object-edit and background-extraction. Input image is the existing Chinese engineering software brand mark. Preserve the camera at upper left, drone at upper right, the crescent ring, city and telecom tower, the cable bundle at lower right, their exact identity, placement, perspective, size, hues and highlights. Change ONLY the rectangular opaque dark navy matte (including top and bottom bars and the negative space behind and inside the crescent) to actual transparent alpha. Keep intentional navy shading within the graphic objects, and preserve fine translucent blue light trails on the cutout without a dark fringe. Compact centered square composition with minimal even padding, clean perimeter at 44-pixel display size. No new drawing, new symbol, border, plate, lettering, gradient panel or shadow. Transparent background, faithful extraction.
```

### 光缆交接箱检查

[cabinet-inspection.webp](../../frontend/public/scenes/cabinet-inspection.webp)

```text
Use case: photorealistic-natural. Horizontal documentary photograph for a municipal telecom engineering case in present-day Hangzhou. A small gray street-side optical distribution cabinet set back along a tiled sidewalk with street trees and modern neighborhood buildings. A technician in muted workwear and a white hard hat checks a paper route sheet beside an open cabinet, from side/back without identifiable face. Modest orderly fiber routing visible only in context at mid-distance, photo about infrastructure in the real streetscape, not a macro product shot, no exposed bare pipe, no neon. Soft overcast natural daylight, believable relative scale and safety cones. No captions, dates, text, logos or graphic overlays. 16:9.
```

### 通信路由现场定位

[route-survey.webp](../../frontend/public/scenes/route-survey.webp)

```text
Use case: photorealistic-natural. Horizontal documentary image of a Chinese telecom civil works supervisor verifying the planned route of underground communications ductwork near a calm tree-lined urban road and a pedestrian intersection in Hangzhou. Worker from behind, hard hat and gray-orange reflective vest, looks at a small survey device on a simple tripod and the existing pavement; subdued work zone fence and another worker in the middle distance. The whole road and worksite context remains visible. Realistic subdued daylight, natural proportions and documentary details, no bare pipe, no visual effects, no readable text, no logos or markup. 16:9.
```
