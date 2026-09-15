import { Bar, Loading } from "../../skeleton";

export default function JobLoading() {
  return (
    <Loading>
      <div className="page-head">
        <Bar width="180px" />
        <Bar width="160px" />
      </div>

      <div className="scroll">
        <table>
          <tbody>
            {Array.from({ length: 6 }, (_, i) => (
              <tr key={i}>
                <td style={{ width: 200 }}>
                  <Bar width="60%" />
                </td>
                <td>
                  <Bar width={i % 2 ? "40%" : "70%"} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Loading>
  );
}
