/**
 * PCB 渲染素材一键导出 / PCB Render Kit
 *
 * 在 PCB 编辑器的顶部菜单「渲染素材包」下提供一键导出：
 *   STEP 3D 模型、DXF 图形层、Gerber、BOM、贴片坐标、网表，
 *   默认打包成一个 ZIP（只需保存一次），也可以单项导出。
 *
 * 本扩展只读取当前 PCB 的制造数据，不修改任何工程内容。
 */

import JSZip from 'jszip';

/** 导出文件名的前缀，需要改直接改这里 */
const BASE_NAME = 'PCB1';

interface ExportedItem {
	label: string;
	fileName: string;
	file: File;
}

interface ExportRequest {
	label: string;
	fileName: string;
	get: () => Promise<File | undefined>;
}

/** 依次导出的文件清单（顺序即 ZIP 内的顺序） */
function buildRequests(): Array<ExportRequest> {
	const md = eda.pcb_ManufactureData;
	const base = BASE_NAME;
	return [
		{
			label: 'STEP 3D 模型（含元件模型）',
			fileName: `${base}_3D.step`,
			get: () => md.get3DFile(
				`${base}_3D`,
				'step',
				['Component Model', 'Via', 'Silkscreen', 'Wire In Signal Layer'],
				'Outfit',
				true,
			),
		},
		{
			label: 'DXF 图形层',
			fileName: `${base}_DXF.dxf`,
			get: () => md.getDxfFile(`${base}_DXF`),
		},
		{
			label: 'Gerber 制版文件',
			fileName: `${base}_Gerber.zip`,
			get: () => md.getGerberFile(`${base}_Gerber`),
		},
		{
			label: 'BOM 物料表',
			fileName: `${base}_BOM.xlsx`,
			get: () => md.getBomFile(`${base}_BOM`, 'xlsx'),
		},
		{
			label: '贴片坐标',
			fileName: `${base}_PickAndPlace.xlsx`,
			get: () => md.getPickAndPlaceFile(`${base}_PickAndPlace`, 'xlsx'),
		},
		{
			label: '网表',
			fileName: `${base}_Netlist.tel`,
			get: () => md.getNetlistFile(`${base}_Netlist`),
		},
	];
}

function formatSize(bytes: number): string {
	if (bytes >= 1024 * 1024)
		return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
	return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function summary(items: Array<ExportedItem>, errors: Array<string>): string {
	const lines = items.map(i => `· ${i.fileName}  (${formatSize(i.file.size)})`);
	if (errors.length)
		lines.push('', '以下项目导出失败：', ...errors.map(e => `· ${e}`));
	return lines.join('\n');
}

/** 逐个导出，单项失败不中断其余 */
async function collect(): Promise<{ items: Array<ExportedItem>; errors: Array<string> }> {
	const items: Array<ExportedItem> = [];
	const errors: Array<string> = [];
	for (const req of buildRequests()) {
		try {
			const file = await req.get();
			if (file)
				items.push({ label: req.label, fileName: req.fileName, file });
			else
				errors.push(`${req.label}：接口返回空`);
		}
		catch (err) {
			errors.push(`${req.label}：${err instanceof Error ? err.message : String(err)}`);
		}
	}
	return { items, errors };
}

/** 一键导出全部并打包成 ZIP（只需选择一次保存位置） */
export async function exportAllBundle(): Promise<void> {
	try {
		const { items, errors } = await collect();
		if (!items.length) {
			eda.sys_Dialog.showInformationMessage(
				`没有导出任何文件。\n\n${errors.join('\n')}`,
				'导出失败',
			);
			return;
		}

		const zip = new JSZip();
		for (const item of items)
			zip.file(item.fileName, item.file);

		zip.file(
			'README.txt',
			[
				`PCB 渲染素材包  ${BASE_NAME}`,
				`导出时间：${new Date().toLocaleString()}`,
				'',
				'包含文件：',
				...items.map(i => `  ${i.fileName}  —  ${i.label}`),
				'',
				'用途：',
				'  *_3D.step             -> Blender / CAD 三维渲染',
				'  *_DXF.dxf             -> 丝印 / 阻焊 / 铜层 / 板框矢量图',
				'  *_Gerber.zip          -> 打样制造',
				'  *_BOM.xlsx            -> 物料采购',
				'  *_PickAndPlace.xlsx   -> SMT 贴片',
				'  *_Netlist.tel         -> 网表 / 仿真',
				errors.length ? `\n导出失败项：\n${errors.join('\n')}` : '',
			].join('\n'),
		);

		const blob = await zip.generateAsync({
			type: 'blob',
			compression: 'DEFLATE',
			compressionOptions: { level: 6 },
		});
		const zipName = `${BASE_NAME}_渲染素材包.zip`;
		await eda.sys_FileSystem.saveFile(new File([blob], zipName), zipName);

		eda.sys_Dialog.showInformationMessage(
			`已导出 ${items.length} 个文件并打包：${zipName}\n\n${summary(items, errors)}`,
			'导出完成',
			'好',
		);
	}
	catch (err) {
		eda.sys_Dialog.showInformationMessage(
			`打包失败：${err instanceof Error ? err.message : String(err)}\n\n可改用「渲染素材包」菜单里的单项导出。`,
			'导出失败',
		);
	}
}

/** 单项导出的通用实现 */
async function exportSingle(
	label: string,
	fileName: string,
	get: () => Promise<File | undefined>,
): Promise<void> {
	try {
		const file = await get();
		if (!file) {
			eda.sys_Dialog.showInformationMessage(`${label}：接口没有返回文件。`, '导出失败');
			return;
		}
		await eda.sys_FileSystem.saveFile(file, fileName);
		eda.sys_Dialog.showInformationMessage(`${label} 已导出：${fileName}`, '导出完成');
	}
	catch (err) {
		eda.sys_Dialog.showInformationMessage(
			`${label} 导出失败：${err instanceof Error ? err.message : String(err)}`,
			'导出失败',
		);
	}
}

export async function exportStep(): Promise<void> {
	const md = eda.pcb_ManufactureData;
	await exportSingle('STEP 3D 模型', `${BASE_NAME}_3D.step`, () => md.get3DFile(
		`${BASE_NAME}_3D`,
		'step',
		['Component Model', 'Via', 'Silkscreen', 'Wire In Signal Layer'],
		'Outfit',
		true,
	));
}

export async function exportDxf(): Promise<void> {
	const md = eda.pcb_ManufactureData;
	await exportSingle('DXF 图形层', `${BASE_NAME}_DXF.dxf`, () => md.getDxfFile(`${BASE_NAME}_DXF`));
}

export async function exportGerber(): Promise<void> {
	const md = eda.pcb_ManufactureData;
	await exportSingle('Gerber 制版文件', `${BASE_NAME}_Gerber.zip`, () => md.getGerberFile(`${BASE_NAME}_Gerber`));
}

export async function exportBom(): Promise<void> {
	const md = eda.pcb_ManufactureData;
	await exportSingle('BOM 物料表', `${BASE_NAME}_BOM.xlsx`, () => md.getBomFile(`${BASE_NAME}_BOM`, 'xlsx'));
}

export async function exportPickPlace(): Promise<void> {
	const md = eda.pcb_ManufactureData;
	await exportSingle('贴片坐标', `${BASE_NAME}_PickAndPlace.xlsx`, () => md.getPickAndPlaceFile(`${BASE_NAME}_PickAndPlace`, 'xlsx'));
}

export async function exportNetlist(): Promise<void> {
	const md = eda.pcb_ManufactureData;
	await exportSingle('网表', `${BASE_NAME}_Netlist.tel`, () => md.getNetlistFile(`${BASE_NAME}_Netlist`));
}

export function about(): void {
	eda.sys_Dialog.showInformationMessage(
		[
			'PCB 渲染素材一键导出  v1.0.0',
			'',
			'在 PCB 编辑器顶部菜单「渲染素材包」中使用。',
			'一键导出 STEP / DXF / Gerber / BOM / 贴片坐标 / 网表，',
			'并打包成一个 ZIP，只需选择一次保存位置。',
			'',
			'本扩展只读取制造数据，不修改工程。',
		].join('\n'),
		'关于',
	);
}

export function activate(): void {
	// 菜单由 extension.json 的 headerMenus 注册，此处无需额外操作。
}
