import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiRequestError } from "../lib/api";
import type { EngineeringObject, EvidenceCapture, ProjectGisSummary, WorkOrder } from "../lib/api";
import type { CaptureMarker } from "../components/gis/GeoMap";
import { GISMapPage } from "./GISMapPage";

vi.mock("../components/gis/GeoMap", () => ({
  GeoMap: ({ captureMarker, objects }: { captureMarker: CaptureMarker | null; objects: EngineeringObject[] }) => (
    <div aria-label="地图数据" data-capture-longitude={captureMarker?.longitude}
      data-design-longitude={(objects[0]?.geometry_wgs84.coordinates as number[][] | undefined)?.[0]?.[0]} />
  ),
}));

const timestamp = "2026-11-05T16:30:00Z";
const first = makeObject("obj-a1", "project-a", "支线区段");
const target = makeObject("obj-a2", "project-a", "目标区段");
const other = makeObject("obj-b1", "project-b", "滨河区段");

function makeObject(id: string, projectId: string, name: string): EngineeringObject {
  return {
    id,
    project_id: projectId,
    design_package_id: "design",
    object_code: id,
    name,
    object_type: "pipe_route",
    source_layer: "pipe_routes",
    source_feature_id: "1",
    geometry_type: "LineString",
    geometry_wgs84: { type: "LineString", coordinates: [[120, 32], [121, 33]] },
    geometry_source_crs_epsg: 4326,
    attributes_snapshot: {},
    expected_rules: {},
    design_version: "1",
    created_at: timestamp,
  };
}

function makeOrder(object: EngineeringObject, status = "assigned"): WorkOrder {
  return {
    id: "order-a",
    project_id: object.project_id,
    engineering_object_id: object.id,
    baseline_id: "baseline-a",
    work_order_code: "区段工单",
    procedure_code: "TRENCH-BEFORE-BACKFILL",
    status,
    design_version: "1",
    design_snapshot: {},
    geometry_snapshot: {},
    rules_snapshot: {},
    spatial_tolerance_m: 50,
    gps_accuracy_threshold_m: 30,
    assigned_to: status === "assigned" ? "施工班组" : null,
    notes: null,
    created_at: timestamp,
    updated_at: timestamp,
  };
}

function makeSummary(id: string, objects: EngineeringObject[], orders: WorkOrder[] = []): ProjectGisSummary {
  return {
    project_id: id,
    design_package_count: 1,
    engineering_object_count: objects.length,
    work_order_count: orders.length,
    objects,
    work_orders: orders,
    truth_note: "",
  };
}

function RouteProbe() {
  const navigate = useNavigate();
  const { search } = useLocation();
  return <><output aria-label="当前路径">{search}</output><button onClick={() => void navigate(-1)}>上一个页面</button></>;
}

function open(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/gis-map${search}`]}>
      <GISMapPage />
      <RouteProbe />
    </MemoryRouter>,
  );
}

describe("工程作业项目与工单选择", () => {
  beforeEach(() => {
    vi.spyOn(api, "listProjects").mockResolvedValue([
      { id: "project-a", code: "A", name: "滨河一区", location: "", manager: null, status: "active", created_at: timestamp },
      { id: "project-b", code: "B", name: "滨河二区", location: "", manager: null, status: "active", created_at: timestamp },
    ]);
    vi.spyOn(api, "listDesignPackages").mockResolvedValue([]);
    vi.spyOn(api, "listWorkOrderCaptures").mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("uses the linked object for a work-order deep link", async () => {
    const linked = { ...makeOrder(other), id: "order-b" };
    vi.spyOn(api, "getWorkOrder").mockResolvedValue(linked);
    vi.spyOn(api, "projectGisSummary").mockImplementation(async (projectId) => projectId === "project-b"
      ? makeSummary("project-b", [other], [linked])
      : makeSummary("project-a", [first, target]));
    open("?workOrder=order-b");

    const inspector = screen.getByRole("complementary", { name: "对象详情" });
    await within(inspector).findByRole("heading", { name: "滨河区段" });
    expect(within(inspector).queryByRole("heading", { name: "支线区段" })).toBeNull();
    await waitFor(() => {
      expect(screen.getByLabelText("当前路径")).toHaveTextContent("project=project-b");
      expect(screen.getByLabelText("当前路径")).toHaveTextContent("object=obj-b1");
    });
    expect(screen.getByRole("link", { name: "工单详情" })).toHaveAttribute("href", "/work-orders/order-b");
  });

  it("opens a mapped project when the latest project only has imagery", async () => {
    vi.mocked(api.listDesignPackages).mockImplementation(async (id) => id === "project-b" ? [{
      id: "design", project_id: id, object_count: 1,
    } as Awaited<ReturnType<typeof api.listDesignPackages>>[number]] : []);
    vi.spyOn(api, "projectGisSummary").mockResolvedValue(makeSummary("project-b", [other]));
    open("");

    await screen.findByRole("button", { name: /滨河区段/ });
    expect(screen.getByRole("combobox", { name: "当前项目" })).toHaveValue("project-b");
    expect(api.projectGisSummary).not.toHaveBeenCalledWith("project-a");
  });

  it("shows the order's frozen design and most recent located capture from a legacy map link", async () => {
    const order = makeOrder(target);
    order.geometry_snapshot.geometry_wgs84 = { type: "LineString", coordinates: [[118, 32], [121, 33]] };
    vi.spyOn(api, "projectGisSummary").mockResolvedValue(makeSummary("project-a", [target], [order]));
    const capture: EvidenceCapture = {
      id: "capture-one", work_order_id: order.id, project_id: order.project_id, evidence_id: "evidence-a",
      verification_job_id: null, client_captured_at: null, server_received_at: timestamp, created_at: timestamp,
      latitude: 32, longitude: 118.001, accuracy_m: 3, location_source: "device_gps", is_synthetic_location: false,
      distance_to_target_m: 4.2, tolerance_m: 50, gps_accuracy_threshold_m: 30, spatial_check_status: "passed",
      spatial_check_reason: "",
    };
    vi.mocked(api.listWorkOrderCaptures).mockResolvedValue([
      capture, { ...capture, id: "without-position", server_received_at: "2026-11-05T16:31:00Z", latitude: null, longitude: null },
    ]);
    open("?project=project-a&object=obj-a2&workOrder=order-a&view=qgis");

    await screen.findByText("4.2 米");
    expect(screen.getByLabelText("地图数据")).toHaveAttribute("data-capture-longitude", "118.001");
    expect(screen.getByLabelText("地图数据")).toHaveAttribute("data-design-longitude", "118");
    expect(screen.getByRole("link", { name: "工单详情" })).toHaveAttribute("href", "/work-orders/order-a");
    expect(screen.getByLabelText("当前路径")).toHaveTextContent("object=obj-a2");
    await waitFor(() => expect(screen.getByLabelText("当前路径")).not.toHaveTextContent("view="));
  });

  it("keeps planned network features out of the work point list and work-order creation", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([
      { id: "project-a", code: "FM-PY-2608", name: "浦沿片区", location: "", manager: null, status: "active", created_at: timestamp },
    ]);
    vi.spyOn(api, "projectGisSummary").mockResolvedValue(makeSummary("project-a", [target]));
    open("?project=project-a");
    const rail = within(screen.getByRole("complementary", { name: "工程对象" }));
    await rail.findByRole("button", { name: /目标区段/ });
    expect(rail.queryByRole("button", { name: /浦沿路.*主干管道/ })).toBeNull();

    await userEvent.click(rail.getByRole("button", { name: "管网布设" }));
    const [layout] = await rail.findAllByRole("button", { name: /浦沿路.*主干管道/ });
    await userEvent.click(layout);
    expect(screen.getByRole("heading", { name: "浦沿路 · 主干管道" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "新建工单" })).toBeNull();
  });

  it("ignores late data from the previous project and restores it on Back", async () => {
    let returnOld!: (summary: ProjectGisSummary) => void;
    const older = new Promise<ProjectGisSummary>((resolve) => { returnOld = resolve; });
    const summary = vi.spyOn(api, "projectGisSummary")
      .mockImplementation((id) => Promise.resolve(id === "project-b"
        ? makeSummary(id, [other])
        : makeSummary(id, [first])))
      .mockReturnValueOnce(older);
    open("?project=project-a");

    await waitFor(() => expect(summary).toHaveBeenCalledWith("project-a"));
    const select = screen.getByRole("combobox", { name: "当前项目" });
    await userEvent.selectOptions(select, "project-b");
    await screen.findByRole("button", { name: /滨河区段/ });
    await act(async () => { returnOld(makeSummary("project-a", [first])); });
    expect(screen.queryByRole("button", { name: /支线区段/ })).toBeNull();
    expect(select).toHaveValue("project-b");

    await userEvent.click(screen.getByRole("button", { name: "上一个页面" }));
    await screen.findByRole("button", { name: /支线区段/ });
    expect(select).toHaveValue("project-a");
  });

  it("retains the saved draft when assignment fails", async () => {
    let orders: WorkOrder[] = [];
    const draft = makeOrder(target, "draft");
    vi.spyOn(api, "projectGisSummary").mockImplementation(async () => makeSummary("project-a", [target], orders));
    const create = vi.spyOn(api, "createWorkOrder").mockImplementation(async () => {
      orders = [draft];
      return draft;
    });
    const assign = vi.spyOn(api, "assignWorkOrder").mockRejectedValue(new ApiRequestError("分派未完成", 500));

    open("?project=project-a&object=obj-a2");
    await userEvent.click(await screen.findByRole("button", { name: "新建工单" }));
    const drawer = screen.getByRole("dialog", { name: "新建施工工单" });
    await userEvent.click(within(drawer).getByRole("button", { name: "创建并分派" }));

    expect(create).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith(draft.id, "现场采集");
    await screen.findByText(/工单 区段工单 已保存。分派未确认/);
    expect(await screen.findByRole("link", { name: "分派工单" })).toHaveAttribute("href", "/work-orders/order-a");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
