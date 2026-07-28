import apiClient from "./index";
import type { FundamentalCandidate, FundamentalFirstDashboard } from "../types/fundamentalFirst";

export const fundamentalFirstApi = {
  getDashboard: async (): Promise<FundamentalFirstDashboard> => {
    const response = await apiClient.get<FundamentalFirstDashboard>("/api/v1/fundamental-first/dashboard", {
      params: { compact: true },
    });
    return response.data;
  },
  getCandidate: async (code: string): Promise<FundamentalCandidate> => {
    const response = await apiClient.get<FundamentalCandidate>(`/api/v1/fundamental-first/candidates/${encodeURIComponent(code)}`);
    return response.data;
  },
};
