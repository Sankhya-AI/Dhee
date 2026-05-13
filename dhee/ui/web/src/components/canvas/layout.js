// Card dimensions per type. Tuned so the most important node types get a
// larger footprint and less-informative ones (result chips) stay compact.
export const CARD_SIZE = {
    workspace: { w: 320, h: 140 },
    project: { w: 260, h: 130 },
    session: { w: 280, h: 150 },
    task: { w: 260, h: 120 },
    result: { w: 240, h: 100 },
    broadcast: { w: 260, h: 110 },
    file: { w: 220, h: 90 },
    asset: { w: 220, h: 100 },
    channel: { w: 240, h: 100 },
};
function size(type) {
    return CARD_SIZE[type] || { w: 220, h: 100 };
}
// Column order under a project. Items in the same column are stacked top-to-
// bottom. Missing types just collapse.
const CHILD_COLUMNS = [
    ["session"],
    ["task", "broadcast"],
    ["result"],
    ["file", "asset"],
];
// Horizontal gap between sibling columns / projects.
const COL_GAP = 48;
const ROW_GAP = 28;
const PROJECT_X_GAP = 160;
const WORKSPACE_TO_PROJECT_Y = 240;
const ORPHAN_Y_OFFSET = 460;
function linkEndpoint(endpoint) {
    if (!endpoint)
        return "";
    if (typeof endpoint === "string")
        return endpoint;
    if (typeof endpoint === "object" && endpoint !== null && "id" in endpoint)
        return String(endpoint.id || "");
    return "";
}
function buildChildMap(nodes, links) {
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const byParent = new Map();
    const claimed = new Set();
    const addChild = (parentId, childId) => {
        const parent = byId.get(parentId);
        const child = byId.get(childId);
        if (!parent || !child)
            return;
        let bucket = byParent.get(parentId);
        if (!bucket) {
            bucket = new Map();
            byParent.set(parentId, bucket);
        }
        const list = bucket.get(child.type) || [];
        if (!list.includes(childId)) {
            list.push(childId);
            bucket.set(child.type, list);
        }
        claimed.add(childId);
    };
    // Use the link graph to associate children to parents. Each project
    // should appear under its workspace; sessions/tasks under their project.
    for (const link of links) {
        const src = linkEndpoint(link.source);
        const tgt = linkEndpoint(link.target);
        const srcNode = byId.get(src);
        const tgtNode = byId.get(tgt);
        if (!srcNode || !tgtNode)
            continue;
        if (srcNode.type === "workspace" && tgtNode.type === "project") {
            addChild(src, tgt);
        }
        else if (srcNode.type === "project" && tgtNode.type === "workspace") {
            addChild(tgt, src);
        }
        else if (srcNode.type === "project") {
            addChild(src, tgt);
        }
        else if (tgtNode.type === "project") {
            addChild(tgt, src);
        }
    }
    // Use meta.projectId / meta.workspaceId as a second-chance parenting
    // mechanism so the layout works even when edges are sparse.
    for (const node of nodes) {
        if (claimed.has(node.id))
            continue;
        const meta = (node.meta || {});
        const parentProjectId = String(meta.projectId || "");
        if (parentProjectId && byId.has(parentProjectId)) {
            addChild(parentProjectId, node.id);
            continue;
        }
        const parentWorkspaceId = String(meta.workspaceId || "");
        if (parentWorkspaceId && byId.has(parentWorkspaceId) && node.type === "project") {
            addChild(parentWorkspaceId, node.id);
        }
    }
    return { byParent };
}
export function layoutGraph(nodes, links) {
    const placed = new Map();
    const childMap = buildChildMap(nodes, links);
    const workspaces = nodes.filter((n) => n.type === "workspace");
    const projects = nodes.filter((n) => n.type === "project");
    const projectParents = new Map();
    for (const [parentId, typesMap] of childMap.byParent) {
        for (const [childType, ids] of typesMap) {
            if (childType === "project") {
                for (const id of ids)
                    projectParents.set(id, parentId);
            }
        }
    }
    // If no workspace nodes exist, synthesize a virtual origin so projects
    // still cluster coherently.
    const rootAnchors = [];
    if (workspaces.length > 0) {
        // Stack workspaces vertically if there are multiple, otherwise origin.
        workspaces.forEach((workspace, idx) => {
            const { w, h } = size(workspace.type);
            const x = -w / 2;
            const y = idx * 260 - h / 2;
            placed.set(workspace.id, { id: workspace.id, x, y, width: w, height: h });
            rootAnchors.push({ id: workspace.id, x: x + w / 2, y: y + h });
        });
    }
    else {
        rootAnchors.push({ id: "__virtual__", x: 0, y: 0 });
    }
    // Group projects by their workspace parent.
    const projectsByRoot = new Map();
    for (const project of projects) {
        const rootId = projectParents.get(project.id) || rootAnchors[0].id;
        const list = projectsByRoot.get(rootId) || [];
        list.push(project);
        projectsByRoot.set(rootId, list);
    }
    // Lay out each project band under its workspace anchor.
    const projectBottoms = new Map(); // projectId → y of its child start
    for (const anchor of rootAnchors) {
        const list = projectsByRoot.get(anchor.id) || [];
        if (list.length === 0)
            continue;
        const projectWidths = list.map((p) => size(p.type).w);
        const totalWidth = projectWidths.reduce((sum, w) => sum + w, 0) + PROJECT_X_GAP * (list.length - 1);
        let cursorX = anchor.x - totalWidth / 2;
        const projectY = anchor.y + WORKSPACE_TO_PROJECT_Y;
        list.forEach((project, idx) => {
            const { w, h } = size(project.type);
            placed.set(project.id, { id: project.id, x: cursorX, y: projectY, width: w, height: h });
            projectBottoms.set(project.id, projectY + h + 40);
            cursorX += w + PROJECT_X_GAP;
            // avoid unused var warning
            void idx;
        });
    }
    // Place children under each project in type-grouped columns.
    for (const [parentId, typesMap] of childMap.byParent) {
        const parent = placed.get(parentId);
        if (!parent)
            continue;
        const baseY = projectBottoms.get(parentId) ?? parent.y + parent.height + 40;
        // Build the columns in canonical order, skipping empty types.
        const columns = [];
        for (const typeGroup of CHILD_COLUMNS) {
            for (const type of typeGroup) {
                const ids = typesMap.get(type) || [];
                if (!ids.length)
                    continue;
                columns.push({ type, ids, w: size(type).w });
            }
        }
        // Include any leftover types we didn't enumerate explicitly.
        for (const [type, ids] of typesMap) {
            if (type === "project")
                continue;
            if (columns.some((col) => col.type === type))
                continue;
            columns.push({ type, ids, w: size(type).w });
        }
        if (!columns.length)
            continue;
        const totalW = columns.reduce((sum, col) => sum + col.w, 0) + COL_GAP * (columns.length - 1);
        let cx = parent.x + parent.width / 2 - totalW / 2;
        for (const col of columns) {
            let cy = baseY;
            for (const id of col.ids) {
                const node = nodes.find((n) => n.id === id);
                if (!node)
                    continue;
                const { w, h } = size(node.type);
                placed.set(id, { id, x: cx + (col.w - w) / 2, y: cy, width: w, height: h });
                cy += h + ROW_GAP;
            }
            cx += col.w + COL_GAP;
        }
    }
    // Orphans — whatever we haven't placed. Park them in a row at the bottom.
    const orphans = nodes.filter((n) => !placed.has(n.id));
    if (orphans.length) {
        let cursorX = 0;
        // Find lowest Y among placed to decide orphan row baseline.
        let lowestY = 0;
        for (const laid of placed.values()) {
            lowestY = Math.max(lowestY, laid.y + laid.height);
        }
        const orphanY = lowestY + ORPHAN_Y_OFFSET;
        for (const orphan of orphans) {
            const { w, h } = size(orphan.type);
            placed.set(orphan.id, { id: orphan.id, x: cursorX, y: orphanY, width: w, height: h });
            cursorX += w + 36;
        }
        // Center the orphan strip under the content.
        const strip = orphans.map((o) => placed.get(o.id)).filter(Boolean);
        if (strip.length) {
            const totalW = strip[strip.length - 1].x + strip[strip.length - 1].width - strip[0].x;
            const shift = -totalW / 2 - strip[0].x;
            for (const laid of strip)
                laid.x += shift;
        }
    }
    const laidNodes = nodes.map((node) => {
        const laid = placed.get(node.id);
        const fallback = size(node.type);
        return {
            ...node,
            x: laid?.x ?? 0,
            y: laid?.y ?? 0,
            width: laid?.width ?? fallback.w,
            height: laid?.height ?? fallback.h,
        };
    });
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of laidNodes) {
        minX = Math.min(minX, n.x);
        minY = Math.min(minY, n.y);
        maxX = Math.max(maxX, n.x + n.width);
        maxY = Math.max(maxY, n.y + n.height);
    }
    if (!isFinite(minX)) {
        minX = -400;
        minY = -300;
        maxX = 400;
        maxY = 300;
    }
    return {
        nodes: laidNodes,
        links,
        bounds: { minX, minY, maxX, maxY },
    };
}
