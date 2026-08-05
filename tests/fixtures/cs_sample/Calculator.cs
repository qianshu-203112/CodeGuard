namespace Demo;

/// 计算器 —— C# 解析器回归 fixture。
public class Calculator
{
    /// 加法。
    public int Add(int a, int b)
    {
        return a + b;
    }

    /// 乘法。
    public int Multiply(int a, int b)
    {
        return a * b;
    }
}

public class Program
{
    public static void Main(string[] args)
    {
        var calc = new Calculator();
        int s = calc.Add(1, 2);
        int m = calc.Multiply(3, 4);
        System.Console.WriteLine($"{s} {m}");
    }
}
