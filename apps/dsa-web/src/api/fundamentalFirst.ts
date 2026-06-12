import apiClient from "./index";
import type { FundamentalFirstDashboard } from "../types/fundamentalFirst";

export const fundamentalFirstApi = {
  getDashboard: async (): Promise<FundamentalFirstDashboard> => {
    const response = await apiClient.get<FundamentalFirstDashboard>("/api/v1/fundamental-first/dashboard");
    return response.data;
  },
};
